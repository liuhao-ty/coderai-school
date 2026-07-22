import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import packageMetadata from "./package.json";

export default defineConfig({
  plugins: [react()],
  define: {
    "import.meta.env.VITE_APP_VERSION": JSON.stringify(packageMetadata.version),
  },
  clearScreen: false,
  build: {
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          antd: ["antd", "@ant-design/icons"],
          flow: ["reactflow"],
          markdown: ["react-markdown", "remark-gfm"]
        }
      }
    }
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000"
    }
  }
});
