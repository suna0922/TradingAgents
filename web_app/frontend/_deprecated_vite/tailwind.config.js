/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        table: {
          wood: '#8B6914',
          dark: '#5C4033',
          light: '#D2B48C',
          bg: '#1a1625',
          card: '#2a2535',
          border: '#3a3545',
        }
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'float': 'float 3s ease-in-out infinite',
        'glow': 'glow 2s ease-in-out infinite alternate',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-8px)' },
        },
        glow: {
          '0%': { boxShadow: '0 0 8px rgba(139, 105, 20, 0.4)' },
          '100%': { boxShadow: '0 0 20px rgba(139, 105, 20, 0.8)' },
        },
      },
    },
  },
  plugins: [],
}
