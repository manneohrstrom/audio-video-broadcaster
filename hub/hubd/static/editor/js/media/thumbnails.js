// Strip thumbnails (made on the hub by hub/ingest/thumbnails.py): small pictures at each
// keyframe (every 2 s), 100 to a JPEG sheet, with index.json listing their file times.

export class CameraThumbnails {
  constructor(url, onLoaded) {
    this.url = url;                 // ".../media-thumbs/<take>/<camera>/"
    this.onLoaded = onLoaded;       // called when the index or a sheet arrives (redraw)
    this.index = null;
    this.sheets = new Map();        // sheet number -> HTMLImageElement
    fetch(url + "index.json")
      .then(response => (response.ok ? response.json() : null))
      .then(index => { this.index = index; if (index) this.onLoaded(); })
      .catch(() => {});
  }

  get ready() {
    return this.index !== null;
  }

  /** Index of the tile nearest to `fileSeconds` (binary search over the keyframe times). */
  nearestTile(fileSeconds) {
    const times = this.index.times_s;
    let low = 0;
    let high = times.length - 1;
    while (low < high) {
      const middle = (low + high) >> 1;
      if (times[middle] < fileSeconds) low = middle + 1; else high = middle;
    }
    if (low > 0 && Math.abs(times[low - 1] - fileSeconds) < Math.abs(times[low] - fileSeconds)) low -= 1;
    return low;
  }

  sheet(number) {
    let image = this.sheets.get(number);
    if (!image) {
      image = new Image();
      image.onload = () => this.onLoaded();
      image.src = this.url + this.index.sheets[number];
      this.sheets.set(number, image);
    }
    return image.complete && image.naturalWidth ? image : null;
  }

  /** Draw the picture nearest `fileSeconds` into the box. False if it isn't loaded yet. */
  draw(context, fileSeconds, x, y, width, height) {
    if (!this.ready) return false;
    const tile = this.nearestTile(fileSeconds);
    const [across, down] = this.index.tiles_per_sheet;
    const [tileWidth, tileHeight] = this.index.tile_size;
    const image = this.sheet(Math.floor(tile / (across * down)));
    if (!image) return false;
    const onSheet = tile % (across * down);
    context.drawImage(image, (onSheet % across) * tileWidth, Math.floor(onSheet / across) * tileHeight,
                      tileWidth, tileHeight, x, y, width, height);
    return true;
  }
}
