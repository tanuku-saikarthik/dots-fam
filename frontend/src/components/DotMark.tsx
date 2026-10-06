import { createAvatar } from '@dicebear/core';
import * as avataaars from '@dicebear/avataaars';
import type { Options as AvataaarsOptions } from '@dicebear/avataaars';
import { useMemo } from 'react';
import type { Dot } from '../api';

const DOT_HEX: Record<string, string> = {
  purple: '7a4dff',
  mint: '0e9f8e',
  orange: 'f26a21',
  blue: '2c6bed',
  ochre: 'c08f00',
  rose: 'e0457b',
};

const PIXELS: Record<'s' | 'm' | 'l', number> = { s: 20, m: 28, l: 46 };

/**
 * Hand-picked "office ensemble" archetypes — a believable, distinct cast of
 * coworkers (the boss, the go-getter, the closer, the creative, the dry
 * analyst...) rather than whatever a random seed happens to roll.
 */
const ARCHETYPES: AvataaarsOptions[] = [
  {
    // the confident boss
    top: ['shortFlat'],
    hairColor: ['724133'],
    facialHair: ['beardLight'],
    facialHairColor: ['724133'],
    facialHairProbability: 100,
    accessoriesProbability: 0,
    clothing: ['blazerAndShirt'],
    clothesColor: ['2c3e50'],
    mouth: ['twinkle'],
    eyes: ['default'],
    eyebrows: ['raisedExcitedNatural'],
    skinColor: ['edb98a'],
  },
  {
    // the go-getter
    top: ['curvy'],
    hairColor: ['a55728'],
    facialHairProbability: 0,
    clothing: ['blazerAndSweater'],
    clothesColor: ['3d3a6b'],
    mouth: ['smile'],
    eyes: ['happy'],
    eyebrows: ['defaultNatural'],
    accessoriesProbability: 0,
    skinColor: ['ffdbb4'],
  },
  {
    // the closer
    top: ['shortWaved'],
    hairColor: ['2c1b18'],
    facialHairProbability: 0,
    clothing: ['shirtCrewNeck'],
    clothesColor: ['2a3142'],
    mouth: ['twinkle'],
    eyes: ['wink'],
    eyebrows: ['raisedExcitedNatural'],
    accessoriesProbability: 0,
    skinColor: ['8d5524'],
  },
  {
    // the quiet creative
    top: ['bun'],
    hairColor: ['4a312c'],
    facialHairProbability: 0,
    clothing: ['collarAndSweater'],
    clothesColor: ['5c4a8a'],
    mouth: ['default'],
    eyes: ['default'],
    eyebrows: ['flatNatural'],
    accessories: ['prescription01'],
    accessoriesProbability: 100,
    skinColor: ['d08b5b'],
  },
  {
    // the dry-humor analyst
    top: ['theCaesarAndSidePart'],
    hairColor: ['b7a69e'],
    facialHairProbability: 0,
    clothing: ['blazerAndShirt'],
    clothesColor: ['3a3f4b'],
    mouth: ['serious'],
    eyes: ['default'],
    eyebrows: ['angryNatural'],
    accessories: ['round'],
    accessoriesProbability: 100,
    skinColor: ['edb98a'],
  },
  {
    // the eager intern
    top: ['shortRound'],
    hairColor: ['c93305'],
    facialHairProbability: 0,
    clothing: ['shirtVNeck'],
    clothesColor: ['4a5a6b'],
    mouth: ['smile'],
    eyes: ['surprised'],
    eyebrows: ['upDownNatural'],
    accessoriesProbability: 0,
    skinColor: ['ffdbb4'],
  },
  {
    // the unbothered veteran
    top: ['shaggyMullet'],
    hairColor: ['6b6b6b'],
    facialHair: ['moustacheFancy'],
    facialHairColor: ['6b6b6b'],
    facialHairProbability: 100,
    accessoriesProbability: 0,
    clothing: ['hoodie'],
    clothesColor: ['5a4a3a'],
    mouth: ['concerned'],
    eyes: ['squint'],
    eyebrows: ['sadConcernedNatural'],
    skinColor: ['8d5524'],
  },
];

/** Dots everyone knows from the default roster get a specific, chosen look. */
const NAMED: Record<string, number> = {
  vance: 0,
  mara: 1,
  cole: 2,
  rina: 3,
  owen: 4,
};

function archetypeFor(name: string): AvataaarsOptions {
  const key = name.trim().toLowerCase();
  if (key in NAMED) return ARCHETYPES[NAMED[key]];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return ARCHETYPES[h % ARCHETYPES.length];
}

export function DotMark({
  dot,
  size = 'm',
  active = false,
}: {
  dot?: Pick<Dot, 'name' | 'color'>;
  size?: 's' | 'm' | 'l';
  active?: boolean;
}) {
  const name = dot?.name?.trim() || 'dot';
  const color = dot?.color ?? 'blue';
  const bg = DOT_HEX[color] ?? DOT_HEX.blue;
  const pixels = PIXELS[size];

  const src = useMemo(
    () =>
      createAvatar(avataaars, {
        seed: name,
        ...archetypeFor(name),
        backgroundColor: [bg],
        backgroundType: ['solid'],
        radius: 50,
      }).toDataUri(),
    [name, bg],
  );

  return (
    <span className={`dot-mark c-${color}${active ? ' active' : ''}`} style={{ width: pixels, height: pixels }}>
      <img src={src} width={pixels} height={pixels} alt="" draggable={false} />
    </span>
  );
}
