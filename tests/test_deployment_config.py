from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicIpDeploymentConfigTests(unittest.TestCase):
    def test_caddy_uses_no_sni_ip_tls_and_hosts_updates(self):
        caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("http://{$CODERAI_PUBLIC_IP}", caddyfile)
        self.assertIn("\n:443 {", caddyfile)
        self.assertNotIn("\nhttps://{$CODERAI_PUBLIC_IP} {", caddyfile)
        self.assertNotIn("default_sni", caddyfile)
        self.assertIn("/etc/letsencrypt/live/coderai-ip/fullchain.pem", caddyfile)
        self.assertIn("handle /.well-known/acme-challenge/*", caddyfile)
        self.assertNotIn("handle_path /.well-known/acme-challenge/*", caddyfile)
        self.assertIn("handle_path /desktop-updates/*", caddyfile)
        self.assertIn("health_interval 2s", caddyfile)
        self.assertIn("format filter", caddyfile)
        self.assertIn("request>headers>X-Coderai-Teacher-Token delete", caddyfile)
        self.assertIn("request>headers>X-Coderai-Student-Token delete", caddyfile)
        self.assertIn("request>headers>Authorization delete", caddyfile)
        self.assertIn("request>headers>Cookie delete", caddyfile)
        self.assertNotIn("CODERAI_DOMAIN", caddyfile)

    def test_compose_mounts_certificate_webroot_and_update_files(self):
        compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("CODERAI_PUBLIC_IP: ${CODERAI_PUBLIC_IP}", compose)
        self.assertIn(":/etc/letsencrypt:ro", compose)
        self.assertIn(":/var/www/certbot:ro", compose)
        self.assertIn(":/srv/coderai/updates:ro", compose)
        self.assertNotIn('"5432:5432"', compose)
        self.assertNotIn('"6379:6379"', compose)

    def test_api_build_supports_an_optional_debian_mirror(self):
        dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("ARG CODERAI_APT_MIRROR=", dockerfile)
        self.assertIn("$CODERAI_APT_MIRROR", dockerfile)
        self.assertIn("CODERAI_APT_MIRROR: ${CODERAI_APT_MIRROR:-}", compose)
        self.assertIn("ARG CODERAI_PIP_INDEX_URL=", dockerfile)
        self.assertIn("--index-url \"$CODERAI_PIP_INDEX_URL\"", dockerfile)
        self.assertIn("CODERAI_PIP_INDEX_URL: ${CODERAI_PIP_INDEX_URL:-}", compose)
        self.assertIn("CODERAI_MAX_CONCURRENT_READS_PER_WORKER", compose)

    def test_ip_certificate_scripts_use_shortlived_profile_and_webroot_renewal(self):
        bootstrap = (ROOT / "deploy" / "bootstrap-ip-certificate.sh").read_text(encoding="utf-8")
        renewal = (ROOT / "deploy" / "renew-ip-certificate.sh").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "systemd" / "coderai-cert-renew.timer").read_text(encoding="utf-8")
        self.assertIn("certbot/certbot:v5.4.0@sha256:", bootstrap)
        self.assertIn("--preferred-profile shortlived", bootstrap)
        self.assertIn('--ip-address "$CODERAI_PUBLIC_IP"', bootstrap)
        self.assertIn("--webroot-path /var/www/certbot", renewal)
        self.assertIn("caddy reload --force", renewal)
        self.assertIn("coderai_tls_certificate_valid_beyond_48h", renewal)
        self.assertIn("OnUnitActiveSec=12h", timer)
        alerts = (ROOT / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
        self.assertIn("CoderAITlsCertificateRenewalRequired", alerts)

    def test_environment_template_requires_ip_instead_of_domain(self):
        environment = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
        self.assertIn("CODERAI_PUBLIC_IP=", environment)
        self.assertIn("CODERAI_CERTBOT_EMAIL=", environment)
        self.assertIn("CODERAI_UPDATE_DIR=", environment)
        self.assertNotIn("CODERAI_DOMAIN=", environment)

    def test_disabled_backup_mode_is_explicit_and_monitored(self):
        compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        alerts = (ROOT / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
        environment = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
        self.assertIn("CODERAI_BACKUP_ENABLED: ${CODERAI_BACKUP_ENABLED:-true}", compose)
        self.assertIn("CODERAI_BACKUP_ENABLED=true", environment)
        self.assertIn("CoderAIIndependentBackupDisabled", alerts)
        deployment = (ROOT / "docs" / "CLOUD_DEPLOYMENT.md").read_text(encoding="utf-8")
        self.assertIn("-o 10001 -g 10001 -m 0755 /srv/coderai/metrics", deployment)

    def test_windows_build_trims_the_encrypted_updater_password(self):
        build_script = (ROOT / "tools" / "build-tauri.ps1").read_text(encoding="utf-8")
        self.assertIn("(Get-Content -LiteralPath $passwordPath -Raw).Trim() | ConvertTo-SecureString", build_script)

    def test_desktop_requests_declare_the_client_version(self):
        api_client = (ROOT / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
        vite_config = (ROOT / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn('"X-CoderAI-Client-Version": CLIENT_VERSION', api_client)
        self.assertIn('"import.meta.env.VITE_APP_VERSION"', vite_config)

    def test_windows_desktop_uses_gui_subsystem_and_native_file_save_plugins(self):
        main = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        library = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        capability = (ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8")
        self.assertIn('cfg_attr(not(debug_assertions), windows_subsystem = "windows")', main)
        self.assertIn("tauri_plugin_dialog::init()", library)
        self.assertIn("tauri_plugin_fs::init()", library)
        self.assertIn('"dialog:allow-save"', capability)
        self.assertIn('"fs:allow-write-file"', capability)

    def test_update_manifest_accepts_current_tauri_nsis_installers(self):
        manifest_script = (ROOT / "tools" / "generate-update-manifest.ps1").read_text(encoding="utf-8")
        self.assertIn('Name.EndsWith("-setup.exe")', manifest_script)
        self.assertIn('Name.EndsWith(".nsis.zip")', manifest_script)
        self.assertIn("Name.Contains($versionMarker)", manifest_script)
        self.assertIn("[Text.UTF8Encoding]::new($false)", manifest_script)


if __name__ == "__main__":
    unittest.main()
