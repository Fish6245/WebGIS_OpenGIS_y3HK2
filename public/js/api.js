// api.js

(() => {
  async function apiPost(url, data = {}) {
    const response = await fetch("./api/" + url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(data),
    });

    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch {
      throw new Error("API trả về dữ liệu không hợp lệ: " + text);
    }
  }

  async function apiGet(url) {
    const response = await fetch("./api/" + url);

    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch {
      throw new Error("API trả về dữ liệu không hợp lệ: " + text);
    }
  }

  window.apiPost = apiPost;
  window.apiGet = apiGet;
})();
