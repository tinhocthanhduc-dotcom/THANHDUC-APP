(function () {
  var queue = document.querySelector("[data-receipt-queue]");
  if (!queue) return;
  function key() {
    var url = new URL(location.href);
    return (
      "receipt-position:" +
      ["method", "from", "to"]
        .map(function (k) {
          return url.searchParams.get(k) || "";
        })
        .join("|")
    );
  }
  document.addEventListener("submit", function (e) {
    if (!queue.contains(e.target) || e.defaultPrevented) return;
    try {
      sessionStorage.setItem(key(), String(scrollY));
    } catch (_) {}
    var row = e.target.closest("[data-receipt-row]"),
      anchor = e.target.querySelector("[name=return_anchor]");
    if (row && anchor) {
      var rows = Array.from(queue.querySelectorAll("[data-receipt-row]")),
        index = rows.indexOf(row);
      anchor.value = (rows[index + 1] || rows[index - 1] || row).id;
    }
  });
  try {
    var position = sessionStorage.getItem(key());
    if (position !== null) {
      sessionStorage.removeItem(key());
      requestAnimationFrame(function () {
        scrollTo(0, Number(position));
      });
    }
  } catch (_) {}
})();
