/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_ORGANIZATION_CODE?: string;
  readonly VITE_APP_VERSION?: string;
  readonly VITE_UPDATER_ENABLED?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
