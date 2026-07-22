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
        self.assertIn("handle_path /desktop-updates/*", caddyfile)
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

    def test_ip_certificate_scripts_use_shortlived_profile_and_webroot_renewal(self):
        bootstrap = (ROOT / "deploy" / "bootstrap-ip-certificate.sh").read_text(encoding="utf-8")
        renewal = (ROOT / "deploy" / "renew-ip-certificate.sh").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "systemd" / "coderai-cert-renew.timer").read_text(encoding="utf-8")
        self.assertIn("certbot/certbot:v5.4.0@sha256:", bootstrap)
        self.assertIn("--preferred-profile shortlived", bootstrap)
        self.assertIn('--ip-address "$CODERAI_PUBLIC_IP"', bootstrap)
        self.assertIn("--webroot-path /var/www/certbot", renewal)
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


if __name__ == "__main__":
    unittest.main()
