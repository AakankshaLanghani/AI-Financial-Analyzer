/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["'IBM Plex Sans'", "sans-serif"],
        mono: ["'IBM Plex Mono'", "monospace"],
      },
      colors: {
        ics: {
          red: "#C31D27",
          dark: "#8B1219",
          light: "#F5E6E7",
        },
      },
    },
  },
  plugins: [],
};
