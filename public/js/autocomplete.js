// autocomplete.js
(() => {
  const addressInput = document.getElementById("keyword");
  const searchWrap = document.querySelector(".search-input");

  const suggestionsDiv = document.getElementById("suggestions");
  suggestionsDiv.className = "suggestions hidden";
  searchWrap.appendChild(suggestionsDiv);

  let suggestionList = [];
  let debounceTimer = null;

  function hideSuggestions() {
    suggestionsDiv.innerHTML = "";
    suggestionsDiv.classList.add("hidden");
  }

  function renderSuggestions() {
    suggestionsDiv.innerHTML = "";

    if (!suggestionList || suggestionList.length === 0) {
      hideSuggestions();
      return;
    }

    suggestionList.forEach((item) => {
      const div = document.createElement("div");
      div.className = "suggestion";

      const name =
        item.name ||
        item.Address_found ||
        item.display_name?.split(",")[0] ||
        "";

      const address =
        item.phuong ||
        item.district ||
        item.QuanHuyen ||
        item.display_name?.split(",").slice(1, 5).join(", ") ||
        "";

      div.innerHTML = `
          <div class="place-row">

              <div class="place-icon">
                  <i class="ri-map-pin-fill"></i>
              </div>

              <div class="place-info">

                  <div class="place-name">
                      ${name}
                  </div>

                  <div class="place-address">
                      ${address}
                  </div>

              </div>

          </div>
      `;

      div.addEventListener("click", () => {
        selectAddress(item);
      });

      suggestionsDiv.appendChild(div);
    });

    suggestionsDiv.classList.remove("hidden");
  }

  async function loadSuggestions() {
    const keyword = addressInput.value.trim();

    if (!keyword) {
      hideSuggestions();
      return;
    }

    try {
      const result = await searchAddresses(keyword);
      suggestionList = result.items || [];
      renderSuggestions();
    } catch (err) {
      console.error(err);
      hideSuggestions();
    }
  }

  function selectAddress(item) {
    const name =
      item.name || item.Address_found || item.display_name?.split(",")[0] || "";
    const lat = parseFloat(item.lat ?? item.Latitude ?? item.latitude);

    const lon = parseFloat(item.lon ?? item.Longitude ?? item.longitude);

    addressInput.value = name;

    if (Number.isFinite(lat)) {
      document.getElementById("lat").value = lat.toFixed(6);
    }

    if (Number.isFinite(lon)) {
      document.getElementById("lon").value = lon.toFixed(6);
    }

    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      if (window.moveMarkerTo) {
        window.moveMarkerTo(lat, lon, name);
      }

      if (window.refreshContext) {
        window.refreshContext(lat, lon, {
          updateOutput: false,
          action: "autocomplete",
        });
      }
    }

    // BỔ SUNG: hiện khung tên đường ngay lập tức
    if (window.setNearestRoadImmediate) {
      window.setNearestRoadImmediate({
        name,
        district: item.district || item.QuanHuyen || "",
        lat: Number.isFinite(lat) ? lat : null,
        lon: Number.isFinite(lon) ? lon : null,
      });
    }
    window.currentAddress = {
      TenDuong: name,
      Phuong: item.phuong || item.ward || item.suburb || item.Phuong || "",
      QuanHuyen: item.district || item.city_district || item.QuanHuyen || "",
      Address_found: item.display_name || "",
    };

    // BỔ SUNG: nearby chạy nền
    if (Number.isFinite(lat) && Number.isFinite(lon) && window.loadNearbyData) {
      window.loadNearbyData(lat, lon);
    }

    hideSuggestions();

    if (window.setOutput) {
      window.setOutput({
        ok: true,
        action: "select_address",
        selected: item,
        lat: Number.isFinite(lat) ? lat : null,
        lon: Number.isFinite(lon) ? lon : null,
      });
    }
  }

  addressInput.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(loadSuggestions, 250);
  });

  addressInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && suggestionList.length > 0) {
      e.preventDefault();
      selectAddress(suggestionList[0]);
    }
  });

  document.addEventListener("click", (e) => {
    if (e.target !== addressInput && !suggestionsDiv.contains(e.target)) {
      hideSuggestions();
    }
  });

  window.selectAddress = selectAddress;
})();
