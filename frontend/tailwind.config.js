/** Rebuild with: npm install && npm run build:css (from frontend/). */
module.exports = {
  darkMode: 'class',
  content: ['../atlasdoc/static/**/*.html'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        carbon: {
          950: '#0a0a0a',
          900: '#111111',
          850: '#161616',
          800: '#1e1e1e',
          700: '#2a2a2a',
          600: '#383838',
        },
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
};
