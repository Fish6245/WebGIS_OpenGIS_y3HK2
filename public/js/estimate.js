// estimate.js

(() => {
  const btnEstimate = document.getElementById("btnEstimate");

  const latInput = document.getElementById("lat");
  const lonInput = document.getElementById("lon");

  btnEstimate.addEventListener("click", async () => {
    const lat = parseFloat(latInput.value);
    const lon = parseFloat(lonInput.value);

    const nearbyRoads = window.currentNearbyRoads || [];
    const nearbyPlaces = window.currentNearbyPlaces || [];
    const address = window.currentAddress || {};

    try {
      const payload = {
        Latitude: lat,
        Longitude: lon,

        TenDuong: address.TenDuong || null,
        Phuong: address.Phuong || null,
        QuanHuyen: address.QuanHuyen || null,

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
      alert(err.message);
    }
  });
})();
