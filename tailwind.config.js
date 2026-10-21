/** @type {import('tailwindcss').Config} */
export default {
  content: ["./templates/**/*.html", "./operations/**/*.py"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#c3c3c3", 900: "#f5f5f5", 850: "#ffffff" },
        cobalt: { 400: "#4f8cff", 500: "#2868e8", 600: "#1f56c7" }
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"], mono: ["IBM Plex Mono", "ui-monospace", "monospace"] }
    }
  },
  plugins: []
}
