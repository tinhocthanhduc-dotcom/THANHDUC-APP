(function () {
  const changed = () => document.dispatchEvent(new Event("tech-photo-change"));
  async function compress(file) {
    const bitmap = await createImageBitmap(file),
      scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas
      .getContext("2d")
      .drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return await new Promise((resolve, reject) =>
      canvas.toBlob(
        (blob) =>
          blob ? resolve(blob) : reject(new Error("Không nén được ảnh")),
        "image/jpeg",
        0.72,
      ),
    );
  }
  document.querySelectorAll("[data-tech-photos]").forEach((box) => {
    const csrf = box.dataset.csrf,
      ticket = box.dataset.ticket;
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
    box
      .querySelector("[data-tech-camera]")
      .addEventListener("change", async function () {
        const files = Array.from(this.files);
        this.value = "";
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
          await upload();
        }
      });
  });
})();
