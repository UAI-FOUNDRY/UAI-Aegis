//! Ed25519 request signing. Must match `backend/app/security/signing.py` byte for byte
//! (docs/CLIENT_PROTOCOL.md sections 2 and 3). The tests at the bottom use vectors
//! produced by the Python reference signer.

use std::time::{SystemTime, UNIX_EPOCH};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD as B64, Engine};
use ed25519_dalek::{Signer, SigningKey};
use rand::{rngs::OsRng, RngCore};
use sha2::{Digest, Sha256};

pub const SIG_VERSION: &str = "AEGIS-SIG-V1";
pub const REGISTER_VERSION: &str = "AEGIS-REGISTER-V1";

pub const HEADER_DEVICE: &str = "X-Aegis-Device";
pub const HEADER_TIMESTAMP: &str = "X-Aegis-Timestamp";
pub const HEADER_NONCE: &str = "X-Aegis-Nonce";
pub const HEADER_SIGNATURE: &str = "X-Aegis-Signature";

/// The four headers every signed exam request carries.
pub struct SignedHeaders {
    pub device: String,
    pub timestamp: String,
    pub nonce: String,
    pub signature: String,
}

/// A brand-new random key (first launch only; then it lives in the keychain).
pub fn generate_key() -> SigningKey {
    SigningKey::generate(&mut OsRng)
}

/// Raw 32-byte public key, base64url without padding.
pub fn public_key_b64(key: &SigningKey) -> String {
    B64.encode(key.verifying_key().to_bytes())
}

/// AEGIS-SIG-V1 \n METHOD \n path \n timestamp \n nonce \n sha256(body) hex
pub fn canonical_message(method: &str, path: &str, timestamp: &str, nonce: &str, body: &[u8]) -> Vec<u8> {
    let body_hash = hex::encode(Sha256::digest(body));
    format!(
        "{SIG_VERSION}\n{}\n{path}\n{timestamp}\n{nonce}\n{body_hash}",
        method.to_uppercase()
    )
    .into_bytes()
}

/// Proof for POST /student/devices: signature of "AEGIS-REGISTER-V1\n<user id>\n<public key>".
pub fn registration_proof(key: &SigningKey, user_id: &str) -> String {
    let message = format!("{REGISTER_VERSION}\n{user_id}\n{}", public_key_b64(key));
    B64.encode(key.sign(message.as_bytes()).to_bytes())
}

/// Sign with an explicit timestamp and nonce (used by the tests).
pub fn sign_request_at(
    key: &SigningKey,
    device_id: &str,
    method: &str,
    path: &str,
    body: &[u8],
    timestamp: &str,
    nonce: &str,
) -> SignedHeaders {
    let signature = key.sign(&canonical_message(method, path, timestamp, nonce, body));
    SignedHeaders {
        device: device_id.to_string(),
        timestamp: timestamp.to_string(),
        nonce: nonce.to_string(),
        signature: B64.encode(signature.to_bytes()),
    }
}

/// Sign with the current time and a fresh random nonce. Call this once per HTTP
/// request, including retries (a reused nonce is rejected as a replay).
pub fn sign_request(key: &SigningKey, device_id: &str, method: &str, path: &str, body: &[u8]) -> SignedHeaders {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
        .to_string();
    let mut nonce = [0u8; 16];
    OsRng.fill_bytes(&mut nonce);
    sign_request_at(key, device_id, method, path, body, &timestamp, &B64.encode(nonce))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Same key as the Python vectors: bytes 0..=31.
    fn key() -> SigningKey {
        let bytes: [u8; 32] = core::array::from_fn(|i| i as u8);
        SigningKey::from_bytes(&bytes)
    }

    const DEVICE: &str = "11111111-2222-3333-4444-555555555555";
    const TS: &str = "1798000000";
    const NONCE: &str = "AAAAAAAAAAAAAAAAAAAAAA";

    #[test]
    fn public_key_matches_python() {
        assert_eq!(public_key_b64(&key()), "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg");
    }

    #[test]
    fn registration_proof_matches_python() {
        assert_eq!(
            registration_proof(&key(), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
            "QZw-_rttxGkFyJ7TgWkMp05AqMww_rzVCLjk9pwmrRqBXFYyTcajksr2f8KBZCQqXXIuCswI0YvfAkctHOkmAA"
        );
    }

    #[test]
    fn signature_with_body_matches_python() {
        // lower-case method on purpose: must be upper-cased like the reference
        let h = sign_request_at(&key(), DEVICE, "post", "/api/v1/student/sessions/x/heartbeat",
                                br#"{"focused":true}"#, TS, NONCE);
        assert_eq!(h.signature,
            "LEz-59gh7Mxt-hSJrkVH2Uw0iwtPMKtu6aYXE9pbYsqntP5SGjkRlanfb2mWQFFjtctHmcXJjCIPe_cqr91-Cw");
    }

    #[test]
    fn signature_with_empty_body_matches_python() {
        let h = sign_request_at(&key(), DEVICE, "POST", "/api/v1/student/sessions/x/finish", b"", TS, NONCE);
        assert_eq!(h.signature,
            "26FGhDPQiOosXAtP3tQbc_70z3yHiSv77zhtxIlp_aHE0cI90BjZ3CKc6gasKjbRYk-9nmq-AhlkbGnMCjZLCw");
    }

    #[test]
    fn every_request_gets_a_new_nonce() {
        let a = sign_request(&key(), DEVICE, "POST", "/p", b"");
        let b = sign_request(&key(), DEVICE, "POST", "/p", b"");
        assert_ne!(a.nonce, b.nonce);
        assert!(B64.decode(&a.nonce).unwrap().len() >= 16);
    }
}
