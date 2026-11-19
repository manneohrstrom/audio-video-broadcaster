// The footer plate's line (user, 2026-11-19: was always "Jazz – Funk – Dub"): a different
// jazz, funk or dub saying on every reload. Attributions are the usual ones; some of these
// are better known than they are documented.

/** @typedef {{ text: string, by?: string }} Quote */

/** @type {Quote[]} */
export const QUOTES = [
  { text: "Jazz – Funk – Dub" },
  { text: "Do not fear mistakes. There are none.", by: "Miles Davis" },
  { text: "Don't play what's there, play what's not there.", by: "Miles Davis" },
  { text: "If you have to ask what jazz is, you'll never know.", by: "Louis Armstrong" },
  { text: "What we play is life.", by: "Louis Armstrong" },
  { text: "It don't mean a thing if it ain't got that swing.", by: "Duke Ellington" },
  { text: "The piano ain't got no wrong notes.", by: "Thelonious Monk" },
  { text: "Damn the rules, it's the feeling that counts.", by: "John Coltrane" },
  { text: "If you don't live it, it won't come out of your horn.", by: "Charlie Parker" },
  { text: "It's taken me all my life to learn what not to play.", by: "Dizzy Gillespie" },
  { text: "Making the simple complicated is commonplace; making the complicated simple, awesomely simple, that's creativity.", by: "Charles Mingus" },
  { text: "If you play a tune and a person don't tap their feet, don't play the tune.", by: "Count Basie" },
  { text: "The only thing better than singing is more singing.", by: "Ella Fitzgerald" },
  { text: "Space is the place.", by: "Sun Ra" },
  { text: "Jazz is not dead, it just smells funny.", by: "Frank Zappa" },
  { text: "I love this club. It's like home: filthy, and full of strangers.", by: "Ronnie Scott" },
  { text: "Free your mind and your ass will follow.", by: "Funkadelic" },
  { text: "Give the drummer some.", by: "James Brown" },
  { text: "On the one." },
  { text: "Close enough for jazz." },
  { text: "Play the music, not the instrument." },
  { text: "There's no such thing as a wrong note, only a badly resolved one." },
  { text: "Dub: the art of taking things away until only the groove is left." },
  { text: "More bass. No, more than that." },
  { text: "Echo… echo… echo…" },
  { text: "Frith Street, 1 a.m., and the band's only just warming up." },
  { text: "From Deptford to Dalston: South London keeps the jazz on the dance floor." },
  { text: "First jazz act to win the Mercury: Ezra Collective, 2023. Dance accordingly." },
  { text: "Brockley: the jazz is coming from inside the house." },
  { text: "Four cameras, one take, no overdubs." },
  { text: "30.011 frames a second: close enough for jazz." },
  { text: "Shutter at 1/50, groove at infinity." },
  { text: "Every take is the first take of the rest of your life." },
  { text: "Count it in, and mean it." },
];

/** A quote at random. @returns {Quote} */
export function pickQuote() {
  return QUOTES[Math.floor(Math.random() * QUOTES.length)];
}
