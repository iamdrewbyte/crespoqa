document.getElementById("login").addEventListener("submit", async e => {
  e.preventDefault();
  const box = document.getElementById("error");
  box.hidden = true;
  try {
    await CQ.call("/api/login", {
      method: "POST",
      json: { username: document.getElementById("u").value.trim(), password: document.getElementById("p").value },
    });
    location.href = "/";
  } catch (err) {
    box.textContent = err.message;
    box.hidden = false;
  }
});
