use keyring_core::{Entry, Error as KeyringError};
use std::collections::HashMap;
use std::sync::OnceLock;
use tauri::Manager;

const CREDENTIAL_SERVICE: &str = "cn.coderai.school";
const AUTH_KEYS: [&str; 3] = [
    "coderai_teacher_token",
    "coderai_teacher_refresh_token",
    "coderai_student_token",
];
static CREDENTIAL_STORE: OnceLock<Result<(), String>> = OnceLock::new();

fn ensure_credential_store() -> Result<(), String> {
    CREDENTIAL_STORE
        .get_or_init(|| {
            let store =
                windows_native_keyring_store::Store::new().map_err(|error| error.to_string())?;
            keyring_core::set_default_store(store);
            Ok(())
        })
        .clone()
}

fn credential_entry(key: &str) -> Result<Entry, String> {
    if !AUTH_KEYS.contains(&key) {
        return Err("不允许访问该安全凭据".to_string());
    }
    ensure_credential_store()?;
    let target = format!("{CREDENTIAL_SERVICE}:{key}");
    Entry::new_with_modifiers(
        CREDENTIAL_SERVICE,
        key,
        &HashMap::from([("target", target.as_str()), ("persistence", "Local")]),
    )
    .map_err(|error| error.to_string())
}

#[tauri::command]
fn set_secret(key: String, value: String) -> Result<(), String> {
    if value.is_empty() {
        return delete_secret(key);
    }
    credential_entry(&key)?
        .set_password(&value)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn get_secret(key: String) -> Result<Option<String>, String> {
    match credential_entry(&key)?.get_password() {
        Ok(value) => Ok(Some(value)),
        Err(KeyringError::NoEntry) => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

#[tauri::command]
fn delete_secret(key: String) -> Result<(), String> {
    match credential_entry(&key)?.delete_credential() {
        Ok(()) | Err(KeyringError::NoEntry) => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}

pub fn run() {
    std::panic::set_hook(Box::new(|panic_info| {
        log::error!("unhandled panic: {panic_info}");
    }));

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            set_secret,
            get_secret,
            delete_secret
        ])
        .setup(|app| {
            log::info!(
                "CoderAI 学堂 {} started; logs: {:?}",
                app.package_info().version,
                app.path().app_log_dir().ok()
            );
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to run CoderAI 学堂");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credential_keys_are_explicitly_limited() {
        assert!(credential_entry("arbitrary_secret").is_err());
        assert!(AUTH_KEYS.iter().all(|key| credential_entry(key).is_ok()));
    }
}
