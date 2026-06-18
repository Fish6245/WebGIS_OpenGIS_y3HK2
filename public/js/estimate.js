// estimate.js

(() => {
  const btnEstimate = document.getElementById("btnEstimate");

  const latInput = document.getElementById("lat");
  const lonInput = document.getElementById("lon");

  const predictionBox = document.getElementById("predictionBox");

  btnEstimate.addEventListener("click", async () => {
    const lat = parseFloat(latInput.value);
    const lon = parseFloat(lonInput.value);

    const nearbyRoads = window.currentNearbyRoads || [];
    const nearbyPlaces = window.currentNearbyPlaces || [];
    const address = window.currentAddress || {};

    try {
      // Hiện trạng thái đang tính toán
      predictionBox.innerHTML = "Đang chờ tính toán...";

      // Khóa nút
      btnEstimate.disabled = true;
      btnEstimate.textContent = "Đang tính...";

      const payload = {
        Latitude: lat,
        Longitude: lon,

        TenDuong: address.TenDuong || "",

        Phuong: address.Phuong || "",
        QuanHuyen: address.QuanHuyen || "",

        TinhThanh: "Hồ Chí Minh",
        ThanhPho: "Hồ Chí Minh",
        QuocGia: "VN",

        nearbyRoads,
        nearbyPlaces,
      };

      console.log("PAYLOAD SEND:", payload);

      const res = await apiPost("predict.php", payload);

      console.log("API RETURN:", res);

      if (window.renderPrediction) {
        window.renderPrediction(res);
      }
    } catch (err) {
      console.error(err);

      predictionBox.innerHTML = "Không thể lấy dữ liệu dự đoán.";
    } finally {
      // Mở lại nút
      btnEstimate.disabled = false;
      btnEstimate.textContent = "Ước lượng giá đất";
    }
  });
})();
