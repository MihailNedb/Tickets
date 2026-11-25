(() => {
  const clock = document.getElementById("live-clock");
  function update() {
    if (clock) clock.textContent = new Date().toLocaleString("ru-RU");
  }
  update();
  setInterval(update, 1000);
})();
