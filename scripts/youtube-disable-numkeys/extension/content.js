document.addEventListener("keydown", (e) => {
  if (e.key >= "0" && e.key <= "9" && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.stopPropagation();
    e.preventDefault();
  }
}, true);
