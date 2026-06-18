// map.js

(() => {
  const map = L.map("map", {}).setView([10.775, 106.7], 13);
  document.getElementById("map").style.cursor = "default";

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  const marker = L.marker([10.775, 106.7], { draggable: true }).addTo(map);

  const latInput = document.getElementById("lat");
  const lonInput = document.getElementById("lon");
  const addressInput = document.getElementById("keyword");
  const predictionBox = document.getElementById("predictionBox");
  const healthBadge = document.getElementById("healthBadge");

  // BỔ SUNG: các khung hiển thị
  const nearbyRoadsBox = document.getElementById("nearbyRoadsBox");
  const nearbyPlacesBox = document.getElementById("nearbyPlacesBox");

  function setLatLon(lat, lon) {
    latInput.value = Number(lat).toFixed(6);
    lonInput.value = Number(lon).toFixed(6);
  }

  function moveMarkerTo(lat, lon, address = "") {
    marker.setLatLng([lat, lon]);
    map.panTo([lat, lon]);
    setLatLon(lat, lon);

    if (address) {
      addressInput.value = address;
    }
  }

  function fmtMeters(m) {
    const n = Number(m);
    if (!Number.isFinite(n)) return "Đang tính...";
    if (n >= 1000) return `${(n / 1000).toFixed(2)} km`;
    return `${Math.round(n)} m`;
  }

  // BỔ SUNG: khung chờ cho nearby
  function setNearbyLoading() {
    nearbyRoadsBox.innerHTML = "Đang chờ tính toán...";
    nearbyPlacesBox.innerHTML = "Đang chờ tính toán...";
  }

  function renderNearbyRoads(items) {
    if (!items || items.length === 0) {
      nearbyRoadsBox.innerHTML = "Không tìm thấy dữ liệu.";
      return;
    }

    const mainRoad = getMainRoadName();

    const filtered = items.filter((item) => {
      const rawName = item.name || item.TenDuong || "";

      if (!rawName || rawName === "Không rõ") {
        return false;
      }

      const name = normalizeRoadName(rawName);

      if (name === mainRoad) {
        return false;
      }

      return true;
    });

    if (filtered.length === 0) {
      nearbyRoadsBox.innerHTML = "Không tải được dữ liệu.";
      return;
    }

    nearbyRoadsBox.innerHTML = filtered
      .map((item) => {
        const name = item.name || item.TenDuong || "Không rõ";
        const district = item.district || item.QuanHuyen || "";
        const dist = fmtMeters(item.distance_m);

        return `
        <div class="road-item">
          <div>${name}</div>
          ${district ? `<div>${district}</div>` : ""}
          <div>${dist}</div>
        </div>
      `;
      })
      .join("");
  }

  function renderNearbyPlaces(items) {
    if (!items || items.length === 0) {
      nearbyPlacesBox.innerHTML = "Không tải được dữ liệu.";
      return;
    }

    const filtered = items.filter((item) => {
      const name = item.name || "";

      return name && name !== "Không rõ";
    });

    if (filtered.length === 0) {
      nearbyPlacesBox.innerHTML = "Không tải được dữ liệu.";
      return;
    }

    nearbyPlacesBox.innerHTML = filtered
      .map((item) => {
        const name = item.name;
        const category = item.category || "";
        const dist = fmtMeters(item.distance_m);

        return `
        <div style="margin-bottom:8px">
          <div>${name}</div>
          <div>${category}</div>
          <div>${dist}</div>
        </div>
      `;
      })
      .join("");
  }

  // BỔ SUNG: gọi riêng nearby ở nền
  async function loadNearbyData(lat, lon) {
    setNearbyLoading();
    try {
      const res = await apiPost("nearby.php", {
        lat,
        lon,
      });

      window.currentNearbyRoads = res.nearbyRoads || [];
      console.log(window.currentNearbyRoads);

      window.currentNearbyPlaces = res.nearbyPlaces || [];

      renderNearbyRoads(window.currentNearbyRoads);

      renderNearbyPlaces(window.currentNearbyPlaces);
    } catch (err) {
      nearbyRoadsBox.innerHTML = "Không tải được dữ liệu.";
      nearbyPlacesBox.innerHTML = "Không tải được dữ liệu.";
      console.error(err);
    }
  }

  async function updateAddressFromPoint(lat, lon) {
    try {
      const res = await reverseGeocode(lat, lon);

      if (!res || !res.ok) {
        return null;
      }

      if (res.address) {
        addressInput.value = res.address;
      }

      window.currentAddress = res.feature || {};

      console.log("CURRENT ADDRESS =", window.currentAddress);

      return res;
    } catch (err) {
      console.error(err);

      window.currentAddress = {};

      return null;
    }
  }

  // BỔ SUNG: hàm trung tâm cho map click / drag / nhập tay
  async function refreshContext(lat, lon) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

    setNearbyLoading();

    await updateAddressFromPoint(lat, lon);
    await loadNearbyData(lat, lon);
  }

  map.on("click", async (e) => {
    const lat = e.latlng.lat;
    const lon = e.latlng.lng;

    moveMarkerTo(lat, lon);
    await refreshContext(lat, lon, { updateOutput: true, action: "map_click" });
  });

  marker.on("dragend", async () => {
    const pos = marker.getLatLng();

    moveMarkerTo(pos.lat, pos.lng);
    await refreshContext(pos.lat, pos.lng, {
      updateOutput: true,
      action: "marker_drag",
    });
  });

  latInput.addEventListener("change", async () => {
    const lat = parseFloat(latInput.value);
    const lon = parseFloat(lonInput.value);

    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

    moveMarkerTo(lat, lon);
    await refreshContext(lat, lon, {
      updateOutput: true,
      action: "manual_latlon",
    });
  });

  lonInput.addEventListener("change", async () => {
    const lat = parseFloat(latInput.value);
    const lon = parseFloat(lonInput.value);

    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

    moveMarkerTo(lat, lon);
    await refreshContext(lat, lon, {
      updateOutput: true,
      action: "manual_latlon",
    });
  });

  window.moveMarkerTo = moveMarkerTo;
  window.refreshContext = refreshContext;
  window.loadNearbyData = loadNearbyData;
  window.renderPrediction = renderPrediction;

  async function healthCheck() {
    try {
      const res = await apiGet("health.php");
      healthBadge.textContent = res.ok ? "API sẵn sàng" : "API lỗi";
      healthBadge.style.background = res.ok ? "#ecfdf3" : "#fef3f2";
      healthBadge.style.color = res.ok ? "#027a48" : "#b42318";
    } catch {
      healthBadge.textContent = "Không kết nối được API";
      healthBadge.style.background = "#fef3f2";
      healthBadge.style.color = "#b42318";
    }
  }
  function normalizeRoadName(name) {
    return String(name || "")
      .toLowerCase()
      .trim()
      .replace(/^hẻm\s+\d+\s+/i, "")
      .replace(/^ngõ\s+\d+\s+/i, "")
      .replace(/^ngách\s+\d+\s+/i, "")
      .replace(/\s+/g, " ");
  }

  function getMainRoadName() {
    const raw =
      window.currentAddress?.TenDuong ||
      window.currentStreetName ||
      addressInput.value ||
      "";

    return normalizeRoadName(raw);
  }
  function formatMoney(value) {
    const n = Number(value);

    if (!Number.isFinite(n)) return "N/A";

    return n.toLocaleString("vi-VN") + ".000 vnđ/m²";
  }

  function renderPrediction(data) {
    console.log("RENDER INPUT:", data);

    const p = data?.prediction || data;

    const isMatched = data?.matched_source === true;

    const sourceLabel = isMatched
      ? "Nguồn kết quả: tìm trong file"
      : "Nguồn kết quả: model dự đoán - độ chính xác có thể không cao";

    if (!p) {
      predictionBox.innerHTML = "Không có dữ liệu dự đoán.";
      return;
    }

    const address =
      [
        data?.matched_info?.TenDuong,
        data?.matched_info?.Phuong,
        data?.matched_info?.QuanHuyen,
        data?.matched_info?.TinhThanh,
      ]
        .filter(Boolean)
        .join(", ") || "Không rõ";

    console.log("matched_source =", data.matched_source);
    console.log("matched_info =", data.matched_info);

    predictionBox.innerHTML = `
    <div class="prediction-card">
      <div class="prediction-item">
        <div class="prediction-label">Giá đất 2019</div>
        <div class="prediction-value">${formatMoney(p.GiaDat2019)}</div>
      </div>

      <div class="prediction-item">
        <div class="prediction-label">Giá đất 2025</div>
        <div class="prediction-value">${formatMoney(p.GiaDat2025)}</div>
      </div>

      <div class="prediction-road">
        ${address}<br>
        <strong>${sourceLabel}</strong>
      </div>
    </div>
  `;
  }
  healthCheck();
})();
