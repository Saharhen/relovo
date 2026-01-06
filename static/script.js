// ----------------------------
// DRAG & DROP PHOTO UPLOADER
// ----------------------------
document.addEventListener("DOMContentLoaded", () => {
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file-input");
  const preview = document.getElementById("preview-grid");

  if (dropZone && fileInput && preview) {

    dropZone.addEventListener("click", () => fileInput.click());

    dropZone.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropZone.classList.add("dragover");
    });

    dropZone.addEventListener("dragleave", () => {
      dropZone.classList.remove("dragover");
    });

    dropZone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropZone.classList.remove("dragover");
      const files = e.dataTransfer.files;
      fileInput.files = files;
      handleFiles(files);
    });

    fileInput.addEventListener("change", (e) => {
      handleFiles(e.target.files);
    });

    function handleFiles(files) {
      const arr = Array.from(files);
      if (arr.length > 10) {
        alert("Можно загрузить максимум 10 фото");
        fileInput.value = "";
        preview.innerHTML = "";
        return;
      }
      preview.innerHTML = "";
      arr.forEach(file => {
        if (!file.type.startsWith("image/")) return;
        const reader = new FileReader();
        reader.onload = (e) => {
          const div = document.createElement("div");
          div.className = "preview-item";
          div.innerHTML = `<img src="${e.target.result}" alt="">`;
          preview.appendChild(div);
        };
        reader.readAsDataURL(file);
      });
    }
  }
});

// ----------------------------
// FILTER POPUPS
// ----------------------------
function openFilter(id, btn) {
  document.querySelectorAll(".filter-popup")
    .forEach(p => p.style.display = "none");

  const popup = document.getElementById("filter-" + id);
  if (!popup) return;

  popup.style.display = "block";
  popup.style.top = (btn.offsetTop + btn.offsetHeight + 10) + "px";
  popup.style.left = btn.offsetLeft + (btn.offsetWidth / 2) + "px";
}

document.addEventListener("click", function (e) {
  if (!e.target.closest(".filter-popup") && !e.target.closest(".fbtn")) {
    document.querySelectorAll(".filter-popup")
      .forEach(p => p.style.display = "none");
  }
});

// ----------------------------
// APPLY FILTERS (LISTINGS)
// ----------------------------
function applyFilters() {
  const city = document.getElementById("city-input")?.value || "";
  const min = document.getElementById("min-price")?.value || "";
  const max = document.getElementById("max-price")?.value || "";
  const type = document.getElementById("type-select")?.value || "";

  const params = new URLSearchParams();

  if (city) params.append("city", city);
  if (min) params.append("min_price", min);
  if (max) params.append("max_price", max);
  if (type) params.append("type", type);

  window.location.href = "/listings?" + params.toString();
}

// =======================================================
// GLOBAL CHAT: Enter = send, Shift+Enter = new line
// =======================================================
document.addEventListener("DOMContentLoaded", () => {

  const chatTextarea = document.getElementById("chat-text");
  const chatForm = document.getElementById("chat-form");
  const chatBox = document.getElementById("chat-messages");

  if (chatTextarea && chatForm) {

    // автоскролл вниз
    if (chatBox) {
      chatBox.scrollTop = chatBox.scrollHeight;
    }

    chatTextarea.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        chatForm.requestSubmit();
      }
    });
  }
});

// =======================================================
// GLOBAL SEARCH (MAIN PAGE): Enter = search
// =======================================================
document.addEventListener("DOMContentLoaded", () => {

  const searchInput = document.getElementById("ai-search-input");

  if (!searchInput) return;

  searchInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (typeof aiSearch === "function") {
        aiSearch();
      }
    }
  });
});
