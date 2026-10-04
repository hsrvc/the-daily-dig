import type { Album } from './parse-albums';

export function pickHero(albums: Album[]): { hero: Album | undefined; rest: Album[] } {
  const indexed = albums.map((album, index) => ({ album, index }));
  const withImage = indexed.filter((x) => x.album.image);
  const candidates = withImage.length > 0 ? withImage : indexed;

  const scored = candidates.map((x) => ({
    ...x,
    score:
      x.album.descriptionEn.length +                          // richer write-up wins
      x.album.descriptionZh.length * 0.3 +                    // bilingual completeness
      (x.album.genre.split('/').length - 1) * 15 +            // genre nuance per slash
      (x.album.year ? 30 : 0),                                // documented year
  }));
  scored.sort((a, b) => b.score - a.score || a.index - b.index);

  const hero = scored[0]?.album;
  const heroIndex = scored[0]?.index ?? 0;
  const rest = albums.filter((_, i) => i !== heroIndex);
  return { hero, rest };
}
