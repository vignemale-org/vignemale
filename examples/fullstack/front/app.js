async function saluer() {
  const nom = document.getElementById("nom").value || "visiteur";
  const r = await fetch(`/api/hello?name=${encodeURIComponent(nom)}`);
  const data = await r.json();
  document.getElementById("sortie").textContent = data.message;
}
