/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/templates/**/*.html",
    "./app/**/*.py",
  ],
  theme: {
    extend: {
      screens: {
        xs: "420px",
      },
    },
  },
  plugins: [],
};
