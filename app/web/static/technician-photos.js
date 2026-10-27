(function () {
  const changed = () => document.dispatchEvent(new Event("tech-photo-change"));
  // Nén ảnh dùng chung đoạn của ui.js (cạnh dài 1280px, JPEG).
  const compress = (file) => (window.tdShrinkPhoto ? window.tdShrinkPhoto(file) : Promise.resolve(file));
  document.querySelectorAll("[data-tech-photos]").forEach((box) => {
    const csrf = box.dataset.csrf,
      ticket = box.dataset.ticket,
      max = Number(box.dataset.photoMax || 0),
      tiles = () => box.querySelectorAll(".tech-photo").length;
    // TUẤN 24/10: mỗi ô tối đa 2 ảnh; đủ thì ẩn nút chụp, xóa bớt thì hiện lại.
    function refresh() {
      if (!max) return;
      const full = tiles() >= max;
      box.querySelector("[data-tech-capture]").hidden = full;
      box.querySelector("[data-tech-full]").hidden = !full;
    }
    document.addEventListener("tech-photo-change", refresh);
    async function remove(button) {
      button.disabled = true;
      try {
        const data = new FormData();
        data.set("csrf", csrf);
        data.set("form_key", crypto.randomUUID());
        data.set("version", button.dataset.version);
        const response = await fetch(
          `/tho/phieu/${ticket}/anh/${button.dataset.photoRemove}/xoa`,
          { method: "POST", body: data, credentials: "same-origin" },
        );
        if (!response.ok) throw new Error("Không xóa được ảnh");
        button.closest(".tech-photo").remove();
        changed();
      } catch (error) {
        button.disabled = false;
        let message = button.parentElement.querySelector("[role=alert]");
        if (!message) {
          message = document.createElement("small");
          message.setAttribute("role", "alert");
          button.parentElement.append(message);
        }
        message.textContent = "Lỗi, thử lại";
      }
    }
    box.addEventListener("click", (e) => {
      const button = e.target.closest("[data-photo-remove]");
      if (button) remove(button);
    });
    box.querySelectorAll("[data-tech-camera]").forEach((input) => input
      .addEventListener("change", async function () {
        let files = Array.from(this.files);
        this.value = "";
        if (max && files.length > max - tiles()) {
          files = files.slice(0, Math.max(0, max - tiles()));
          if (window.tdToast) window.tdToast(`Mỗi ô tối đa ${max} ảnh`, "warn");
        }
        const uploads = [];
        for (const file of files) {
          const tile = document.createElement("article");
          tile.className = "tech-photo";
          tile.innerHTML =
            '<img alt="Ảnh đang tải"><small>Đang tải…</small><button type="button" hidden>Lỗi, thử lại</button>';
          box.querySelector(".tech-photo-list").append(tile);
          const preview = URL.createObjectURL(file);
          tile.querySelector("img").src = preview;
          const retry = tile.querySelector("button");
          async function upload() {
            tile.dataset.uploading = "1";
            retry.hidden = true;
            tile.querySelector("small").textContent = "Đang tải…";
            changed();
            try {
              // Máy cũ không nén được (thiếu createImageBitmap, ảnh lạ) thì gửi ảnh gốc, server tự nén.
              const blob = await compress(file).catch(() => file),
                data = new FormData();
              data.set("csrf", csrf);
              data.set("form_key", crypto.randomUUID());
              data.set("tag", box.dataset.photoTag);
              data.set(
                "file",
                blob,
                file.name.replace(/\.[^.]*$/, "") + ".jpg",
              );
              const response = await fetch(`/tho/phieu/${ticket}/anh`, {
                method: "POST",
                body: data,
                credentials: "same-origin",
              });
              if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.error || "Lỗi tải ảnh");
              }
              const saved = await response.json();
              tile.dataset.savedPhoto = "";
              tile.dataset.photoId = saved.id;
              tile.querySelector("img").src = saved.url;
              tile.querySelector("small").textContent = "Đã lưu";
              const button = document.createElement("button");
              button.type = "button";
              button.dataset.photoRemove = saved.id;
              button.dataset.version = saved.version;
              button.textContent = "✕ Xóa";
              tile.append(button);
              URL.revokeObjectURL(preview);
            } catch (error) {
              tile.querySelector("small").textContent = error.message && error.message !== "Lỗi tải ảnh" ? error.message : "Lỗi, thử lại";
              retry.hidden = false;
            } finally {
              delete tile.dataset.uploading;
              changed();
            }
          }
          retry.addEventListener("click", upload);
          uploads.push(upload());  // tải song song các ảnh vừa chọn, không chờ từng ảnh
        }
        await Promise.all(uploads);
      }));
  });
})();
