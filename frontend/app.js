const statusText = document.getElementById("status-text");

if (statusText) {
  statusText.textContent = "Static frontend scaffolding is live. Add API integration and visualization logic in frontend/app.js.";
}

// Example placeholder for future stats API integration:
// const apiOrigin = "https://your-cloudfront-domain.cloudfront.net";
// const statsEndpoint = "https://<api-id>.execute-api.<region>.amazonaws.com/stats/summary";
//
// async function loadSummary() {
//   const response = await fetch(statsEndpoint, { credentials: "omit" });
//   const data = await response.json();
//   console.log(data);
// }
//
// loadSummary();
