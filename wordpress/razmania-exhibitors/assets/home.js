/* RazMania Exhibitors — homepage placement of the "Best of RazMania" strip.
 *
 * The homepage story grid is a Cornerstone Looper: not writable over REST and
 * wiped by any builder re-save, so nothing can be templated into it. The
 * RazMania Event plugin solved this by injecting after the hub's event module
 * on the client; this does the same, right after that module (or after the
 * event plugin's own block when it is there), so the three read as one
 * sequence: who you'll meet, what it costs, who to see first.
 */
( function () {
  'use strict';
  if ( typeof window.RZX_HOME === 'undefined' || ! window.RZX_HOME.markup ) { return; }
  if ( document.querySelector( '.rzx-strip' ) ) { return; }

  function place() {
    var anchor = document.querySelector( '.rzm-hub .rzme' )
      || document.querySelector( '.rzm-hub .rzm-ev' )
      || document.querySelector( '.rzm-hub .front' );
    if ( ! anchor ) { return false; }
    var tmp = document.createElement( 'div' );
    tmp.innerHTML = window.RZX_HOME.markup;
    var node = tmp.firstElementChild;
    if ( ! node ) { return false; }
    anchor.parentNode.insertBefore( node, anchor.nextSibling );
    return true;
  }

  if ( ! place() ) {
    var tries = 0;
    var iv = setInterval( function () { tries++; if ( place() || tries > 20 ) { clearInterval( iv ); } }, 300 );
  }
} )();
