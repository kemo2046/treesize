import { defineConfig } from 'vite';
import path from 'path';

export default defineConfig({
  root: path.resolve(__dirname),
  build: {
    outDir: path.resolve(__dirname, '..', '..', 'dist', 'renderer'),
    emptyOutDir: true,
  },
});
