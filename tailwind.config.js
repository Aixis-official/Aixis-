/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/aixis_web/templates/**/*.html',
    './src/aixis_web/static/js/**/*.js',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        aixis: {
          50: '#eef2ff',
          100: '#e0e7ff',
          200: '#c7d2fe',
          300: '#a5b4fc',
          400: '#818cf8',
          500: '#1a365d',
          600: '#162d4d',
          700: '#0f1f36',
          800: '#0a1628',
          900: '#060d19'
        },
        grade: {
          s: '#d4af37',
          a: '#38a169',
          b: '#2b6cb0',
          c: '#ed8936',
          d: '#e53e3e'
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        jp: ['"Noto Sans JP"', 'sans-serif'],
        mixed: ['Inter', '"Noto Sans JP"', 'sans-serif'],
        serif: ['"Noto Serif JP"', 'serif'],
      },
      letterSpacing: {
        'heading': '-0.02em'
      },
      lineHeight: {
        'body-jp': '1.9'
      },
      maxWidth: {
        'prose-narrow': '760px',
        'full-bleed': '1200px'
      }
    }
  },
  plugins: [],
}
