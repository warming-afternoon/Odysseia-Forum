/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        discord: {
          bg: '#36393f',
          sidebar: '#2f3136',
          element: '#202225',
          primary: '#5865F2',
          hover: '#4752c4',
          green: '#3ba55c',
          red: '#ed4245',
          text: '#dcddde',
          muted: '#72767d',
          dark: '#18191c',
        },
      },
      transitionTimingFunction: {
        'bounce-in': 'cubic-bezier(0.34, 1.56, 0.64, 1)',
      },
    },
  },
  plugins: [],
}
