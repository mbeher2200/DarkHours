// Injects the Google Fonts stylesheet from script (see index.html for why)
// so it's fetched and applied without being render-blocking. Loaded with
// `defer`, so this runs after the document has parsed.
(function () {
  var link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = 'https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;700&display=optional'
  document.head.appendChild(link)
})()
