async function greet() {
  const name = document.getElementById("name").value || "visitor";
  const r = await fetch(`/api/hello?name=${encodeURIComponent(name)}`);
  const data = await r.json();
  document.getElementById("output").textContent = data.message;
}
