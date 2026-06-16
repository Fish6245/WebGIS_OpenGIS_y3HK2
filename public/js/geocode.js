// geocode.js

(() => {
  async function searchAddresses(keyword) {
    return await apiPost("search.php", { keyword });
  }

  async function reverseGeocode(lat, lon) {
    return await apiPost("reverse.php", { lat, lon });
  }

  window.searchAddresses = searchAddresses;
  window.reverseGeocode = reverseGeocode;
})();
