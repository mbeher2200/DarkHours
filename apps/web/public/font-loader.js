// Applies the preloaded Google Fonts stylesheet (index.html) without it being
// a render-blocking <link rel="stylesheet">. Loaded with `defer` in
// index.html — that's safe here specifically because of the timeout below:
// the preload's "load" event isn't reliable enough across browsers/network
// conditions to depend on alone, so this also force-applies on a timer as a
// fallback. Guarded so it only runs once regardless of which fires first.
(function () {
  var link = document.getElementById('gfont-preload')
  if (!link) return
  var applied = false
  var apply = function () {
    if (applied) return
    applied = true
    link.rel = 'stylesheet'
  }
  link.addEventListener('load', apply)
  setTimeout(apply, 3000)
})()
