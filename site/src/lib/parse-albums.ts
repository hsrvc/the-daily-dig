export interface Album {
  artist: string;
  album: string;
  genre: string;
  year: string;
  source: string;
  image: string;
  postUrl: string;
  descriptionEn: string;
  descriptionZh: string;
}

export function parseAlbums(markdown: string): Album[] {
  const albums: Album[] = [];
  const sections = markdown.split(/^## /m).filter((s) => s.trim());

  for (const section of sections) {
    const lines = section.split('\n');
    const titleLine = lines[0].trim();

    const titleMatch = titleLine.match(/^(.+?)\s*[—–]\s*(.+)$/);
    if (!titleMatch) continue;

    const artist = titleMatch[1].trim();
    const album = titleMatch[2].trim();

    const getField = (name: string): string => {
      const re = new RegExp(`\\*\\*${name}:\\*\\*\\s*(.+)`, 'i');
      for (const line of lines) {
        const m = line.match(re);
        if (m) return m[1].trim();
      }
      return '';
    };

    albums.push({
      artist,
      album,
      genre: getField('Genre'),
      year: getField('Year'),
      source: getField('Source'),
      image: getField('Image'),
      postUrl: getField('Post URL'),
      descriptionEn: getField('Description EN'),
      descriptionZh: getField('Description ZH'),
    });
  }

  return albums;
}
