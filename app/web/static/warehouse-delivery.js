(function () {
  var form = document.querySelector("[data-warehouse-deliver]"),
    button = document.querySelector("[data-warehouse-submit]");
  if (!form || !button) return;
  function update() {
    var full = Array.from(form.querySelectorAll("[data-remaining]")).every(
      function (input) {
        return (
          input.value.trim() !== "" &&
          Number(input.value.replace(",", ".")) ===
            Number(input.dataset.remaining)
        );
      },
    );
    button.textContent = full ? button.dataset.fullLabel || "Giao đủ" : "Giao một phần";
  }
  form.addEventListener("input", update);
  update();
})();
