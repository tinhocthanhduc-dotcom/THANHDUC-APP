/* Báo trùng khách khi rời ô SĐT/MST. Chỉ báo sớm; máy chủ vẫn kiểm khi Lưu. */
(function () {
  var form = document.querySelector("[data-dup-form]");
  if (!form) return;
  var timers = {};
  function kind() {
    var k = form.querySelector("[name=kind]:checked");
    return k ? k.value : "CA_NHAN";
  }
  function check(input) {
    var target = document.querySelector(input.getAttribute("data-dup-target"));
    if (!target) return;
    var fields = form.querySelectorAll(
      "[data-dup-target='" + input.getAttribute("data-dup-target") + "']",
    );
    var phone = [];
    var tax = "";
    fields.forEach(function (f) {
      if (!f.value.trim()) return;
      if (f.getAttribute("data-dup-check") === "tax") tax = f.value.trim();
      else phone.push(f.value.trim());
    });
    if (!phone.length && !tax) {
      target.innerHTML = "";
      return;
    }
    var q = new URLSearchParams({
      phone: phone.join(","),
      tax: tax,
      kind: kind(),
      except: form.getAttribute("data-dup-except") || "",
      quay_ve: form.getAttribute("data-dup-return") || "",
    });
    fetch("/khach-hang/kiem-trung?" + q.toString(), {
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.ok ? r.text() : "";
      })
      .then(function (html) {
        target.innerHTML = html;
      })
      .catch(function () {}); // lỗi mạng: im lặng, không chặn người dùng
  }
  function schedule(input) {
    var key = input.getAttribute("data-dup-target");
    clearTimeout(timers[key]);
    timers[key] = setTimeout(function () {
      check(input);
    }, 300);
  }
  ["change", "focusout"].forEach(function (name) {
    form.addEventListener(name, function (e) {
      if (e.target.matches && e.target.matches("[data-dup-check]"))
        schedule(e.target);
    });
  });
})();
/* Đợt 3: Cho nợ mới hiện ô Hạn mức nợ. */
(function () {
  var box = document.querySelector("[data-kh-credit]");
  if (!box) return;
  var limit = box.querySelector("[data-kh-limit]");
  box.addEventListener("change", function (e) {
    if (e.target.matches("[data-kh-debt]")) limit.hidden = e.target.value !== "1" && !limit.querySelector("input").value.replace(/[0.]/g, "");
  });
})();
