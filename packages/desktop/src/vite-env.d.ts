/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Dev-only: "<port>:<token>" to talk to a backend started by hand. */
  readonly VITE_JLT_BACKEND?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
