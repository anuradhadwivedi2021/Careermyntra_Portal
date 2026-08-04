if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then((reg) => console.log('Service worker registered:', reg.scope))
      .catch((err) => console.log('Service worker registration failed:', err));
  });
}