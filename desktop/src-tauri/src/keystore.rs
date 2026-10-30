//! The device's private key lives only in the OS keychain
//! (Windows Credential Manager, macOS Keychain, Linux Secret Service), never in a file.
//!
//! One key per student account on this laptop: the server binds a key to one user,
//! so two students sharing a laptop each get their own key.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD as B64, Engine};
use ed25519_dalek::SigningKey;
use keyring::{Entry, Error as KeyringError};

use crate::signing;

const SERVICE: &str = "uai-aegis";

fn entry(name: &str) -> Result<Entry, String> {
    Entry::new(SERVICE, name).map_err(|e| format!("Can't open the system keychain: {e}"))
}

/// Load this user's device key, or create and store a new one on first use.
pub fn load_or_create_key(user_id: &str) -> Result<SigningKey, String> {
    let entry = entry(&format!("device-key:{user_id}"))?;
    match entry.get_password() {
        Ok(stored) => {
            let bytes = B64.decode(stored.trim()).map_err(|_| "Stored device key is damaged.".to_string())?;
            let bytes: [u8; 32] = bytes.try_into().map_err(|_| "Stored device key is damaged.".to_string())?;
            Ok(SigningKey::from_bytes(&bytes))
        }
        Err(KeyringError::NoEntry) => {
            let key = signing::generate_key();
            entry
                .set_password(&B64.encode(key.to_bytes()))
                .map_err(|e| format!("Can't save the device key to the system keychain: {e}"))?;
            Ok(key)
        }
        Err(e) => Err(format!("Can't read the system keychain: {e}")),
    }
}

/// Key for the local encrypted draft copies (drafts.rs), one per user.
pub fn load_or_create_draft_key(user_id: &str) -> Result<[u8; 32], String> {
    let entry = entry(&format!("draft-key:{user_id}"))?;
    match entry.get_password() {
        Ok(stored) => B64
            .decode(stored.trim())
            .ok()
            .and_then(|b| b.try_into().ok())
            .ok_or_else(|| "Stored draft key is damaged.".to_string()),
        Err(KeyringError::NoEntry) => {
            let mut key = [0u8; 32];
            rand::RngCore::fill_bytes(&mut rand::rngs::OsRng, &mut key);
            entry
                .set_password(&B64.encode(key))
                .map_err(|e| format!("Can't save the draft key to the system keychain: {e}"))?;
            Ok(key)
        }
        Err(e) => Err(format!("Can't read the system keychain: {e}")),
    }
}

/// The device id is not secret, but keeping it avoids re-registering on every login
/// (registration is rate limited).
pub fn load_device_id(user_id: &str) -> Option<String> {
    entry(&format!("device-id:{user_id}")).ok()?.get_password().ok()
}

pub fn save_device_id(user_id: &str, device_id: &str) {
    if let Ok(e) = entry(&format!("device-id:{user_id}")) {
        let _ = e.set_password(device_id);
    }
}

pub fn forget_device_id(user_id: &str) {
    if let Ok(e) = entry(&format!("device-id:{user_id}")) {
        let _ = e.delete_credential();
    }
}
