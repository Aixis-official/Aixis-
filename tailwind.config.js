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
          s: '#DDC67D',
          a: '#8BB2CA',
          b: '#9DB9AD',
          c: '#B9ABA0',
          d: '#B98D8D'
        }
      },
      fontFamily: {
        sans: ['"Noto Serif JP"', '"Hiragino Mincho ProN"', 'serif'],
        jp: ['"Noto Serif JP"', '"Hiragino Mincho ProN"', 'serif'],
        mixed: ['"Noto Serif JP"', '"Hiragino Mincho ProN"', 'serif'],
        serif: ['"Noto Serif JP"', '"Hiragino Mincho ProN"', 'serif'],
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
