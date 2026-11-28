// Aplica o tema (claro/escuro) antes de a página aparecer, para não piscar. Escolha guardada só neste navegador.
(() => {
  let tema = null;
  try { tema = localStorage.getItem("sisco.tema"); } catch (_) { /* sem armazenamento: segue o Windows */ }
  if (tema !== "claro" && tema !== "escuro") tema = window.matchMedia("(prefers-color-scheme: dark)").matches ? "escuro" : "claro";
  document.documentElement.dataset.theme = tema === "escuro" ? "dark" : "light";
})();
