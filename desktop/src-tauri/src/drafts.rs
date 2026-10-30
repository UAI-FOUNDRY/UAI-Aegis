//! Local encrypted copy of every answer (CLIENT_PROTOCOL.md section 4: "keep a local
//! encrypted copy so a Wi-Fi drop loses nothing; resend when back").
//!
//! One file per exam session: `<app data>/drafts/<user id>/<session id>.bin`
//! = 12-byte nonce + AES-256-GCM(JSON), with the session id as associated data so a
//! file copied onto another session's name fails to decrypt. The key lives in the
//! OS keychain, never on disk.
//!
//! The local copy is written BEFORE each save/run/submit is sent, so it is always at
//! least as new as the server's copy (only this laptop can write the session).

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LocalDraft {
    pub language: String,
    pub code: String,
    /// true once the server has confirmed this exact code
    pub synced: bool,
    pub revision: Option<i64>,
}

pub type SessionDrafts = HashMap<String, LocalDraft>;

pub struct DraftStore {
    dir: PathBuf,
    cipher: Aes256Gcm,
}

impl DraftStore {
    pub fn new(dir: PathBuf, key: &[u8; 32]) -> Result<Self, String> {
        fs::create_dir_all(&dir).map_err(|e| format!("Can't create the drafts folder: {e}"))?;
        let cipher = Aes256Gcm::new_from_slice(key).map_err(|_| "Bad draft key.".to_string())?;
        Ok(Self { dir, cipher })
    }

    fn path(&self, session_id: &str) -> PathBuf {
        self.dir.join(format!("{session_id}.bin"))
    }

    /// Everything saved locally for this session. Missing or damaged file -> empty.
    pub fn load(&self, session_id: &str) -> SessionDrafts {
        let Ok(raw) = fs::read(self.path(session_id)) else { return HashMap::new() };
        if raw.len() < 12 {
            return HashMap::new();
        }
        let (nonce, ciphertext) = raw.split_at(12);
        let Ok(plain) = self
            .cipher
            .decrypt(Nonce::from_slice(nonce), Payload { msg: ciphertext, aad: session_id.as_bytes() })
        else {
            return HashMap::new();
        };
        serde_json::from_slice(&plain).unwrap_or_default()
    }

    fn store(&self, session_id: &str, drafts: &SessionDrafts) -> Result<(), String> {
        let plain = serde_json::to_vec(drafts).map_err(|e| e.to_string())?;
        let mut nonce = [0u8; 12];
        OsRng.fill_bytes(&mut nonce);
        let ciphertext = self
            .cipher
            .encrypt(Nonce::from_slice(&nonce), Payload { msg: &plain, aad: session_id.as_bytes() })
            .map_err(|_| "Could not encrypt the draft.".to_string())?;
        let mut out = nonce.to_vec();
        out.extend_from_slice(&ciphertext);
        // Write to a temp file, then rename, so a crash mid-write never leaves half a file.
        let tmp = self.dir.join(format!("{session_id}.tmp"));
        fs::write(&tmp, out).map_err(|e| format!("Could not save the draft: {e}"))?;
        fs::rename(&tmp, self.path(session_id)).map_err(|e| format!("Could not save the draft: {e}"))
    }

    /// Save the latest code before it goes to the server (not yet synced).
    pub fn put_unsynced(&self, session_id: &str, question_id: &str, language: &str, code: &str,
                        revision: Option<i64>) -> Result<(), String> {
        let mut drafts = self.load(session_id);
        drafts.insert(question_id.to_string(), LocalDraft {
            language: language.to_string(),
            code: code.to_string(),
            synced: false,
            revision,
        });
        self.store(session_id, &drafts)
    }

    /// The server confirmed `code`. Only mark it synced if nothing newer was written since.
    pub fn mark_synced(&self, session_id: &str, question_id: &str, code: &str, revision: Option<i64>) {
        let mut drafts = self.load(session_id);
        if let Some(d) = drafts.get_mut(question_id) {
            if d.code == code {
                d.synced = true;
                d.revision = revision;
                let _ = self.store(session_id, &drafts);
            }
        }
    }

    /// After the exam is handed in the local copy is no longer needed.
    pub fn remove_session(&self, session_id: &str) {
        let _ = fs::remove_file(self.path(session_id));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store(name: &str) -> DraftStore {
        let dir = std::env::temp_dir().join(format!("aegis-drafts-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        DraftStore::new(dir, &[7u8; 32]).unwrap()
    }

    #[test]
    fn round_trip_and_sync() {
        let s = store("rt");
        s.put_unsynced("s1", "q1", "python3", "print(1)", Some(2)).unwrap();
        let d = &s.load("s1")["q1"];
        assert_eq!((d.code.as_str(), d.synced, d.revision), ("print(1)", false, Some(2)));

        s.mark_synced("s1", "q1", "print(1)", Some(3));
        assert!(s.load("s1")["q1"].synced);

        // A newer local edit must not be marked synced by an older server reply.
        s.put_unsynced("s1", "q1", "python3", "print(2)", Some(3)).unwrap();
        s.mark_synced("s1", "q1", "print(1)", Some(4));
        assert!(!s.load("s1")["q1"].synced);
    }

    #[test]
    fn file_is_encrypted_and_bound_to_its_session() {
        let s = store("enc");
        s.put_unsynced("s1", "q1", "python3", "very secret code", None).unwrap();
        let raw = fs::read(s.path("s1")).unwrap();
        assert!(!String::from_utf8_lossy(&raw).contains("very secret code"));

        // Copy session 1's file onto session 2's name: must not decrypt.
        fs::copy(s.path("s1"), s.path("s2")).unwrap();
        assert!(s.load("s2").is_empty());

        // Tamper with one byte: must not decrypt.
        let mut bad = raw.clone();
        let last = bad.len() - 1;
        bad[last] ^= 1;
        fs::write(s.path("s1"), bad).unwrap();
        assert!(s.load("s1").is_empty());
    }
}
