"""Generate backend/src/padlet/data/names.txt — alliterative "Adjective Animal" names.

Run once (the output is committed):  python scripts/generate_names.py
Editing the word lists below and re-running is safe: existing comments keep the
name they were posted with (names are stored on each comment).
"""
from pathlib import Path

ADJECTIVES = {
    "a": ["Agile", "Amazing", "Artful", "Awesome", "Adventurous", "Amiable"],
    "b": ["Bouncy", "Brave", "Bubbly", "Bashful", "Brilliant", "Breezy", "Bold"],
    "c": ["Curious", "Cheerful", "Clever", "Cosmic", "Cuddly", "Calm", "Chatty"],
    "d": ["Dizzy", "Daring", "Dapper", "Dreamy", "Dancing", "Delightful"],
    "e": ["Eager", "Elegant", "Epic", "Energetic", "Easygoing"],
    "f": ["Fluffy", "Fancy", "Fearless", "Friendly", "Funky", "Fuzzy", "Feisty"],
    "g": ["Giggly", "Groovy", "Gentle", "Glorious", "Grumpy", "Goofy", "Gallant"],
    "h": ["Happy", "Hasty", "Humble", "Hungry", "Hilarious", "Hopeful"],
    "i": ["Itchy", "Inventive", "Insightful", "Incredible", "Imaginative"],
    "j": ["Jolly", "Jumpy", "Jazzy", "Jovial", "Jittery"],
    "k": ["Kind", "Kooky", "Keen", "Knightly", "Kingly"],
    "l": ["Lazy", "Lucky", "Lively", "Loyal", "Lovely", "Lanky", "Laughing"],
    "m": ["Merry", "Mighty", "Mellow", "Mysterious", "Mischievous", "Majestic"],
    "n": ["Nimble", "Nifty", "Noble", "Nervous", "Nutty", "Nosy"],
    "o": ["Odd", "Optimistic", "Outgoing", "Orange", "Observant"],
    "p": ["Playful", "Peppy", "Polite", "Puzzled", "Plucky", "Proud", "Perky"],
    "q": ["Quick", "Quiet", "Quirky", "Quizzical"],
    "r": ["Rapid", "Radiant", "Rowdy", "Rusty", "Relaxed", "Rambunctious"],
    "s": ["Sleepy", "Sneaky", "Sassy", "Silly", "Speedy", "Sunny", "Snazzy"],
    "t": ["Tiny", "Thoughtful", "Tidy", "Tipsy", "Turbo", "Twinkly", "Tenacious"],
    "u": ["Upbeat", "Unique", "Ultra", "Unruffled"],
    "v": ["Valiant", "Vivid", "Velvet", "Vigilant", "Vibrant"],
    "w": ["Witty", "Wobbly", "Wise", "Wild", "Wandering", "Whimsical"],
    "y": ["Yawning", "Youthful", "Yummy", "Yodeling"],
    "z": ["Zesty", "Zany", "Zealous", "Zippy", "Zen"],
}

ANIMALS = {
    "a": ["Alpaca", "Anteater", "Armadillo", "Axolotl", "Albatross", "Antelope"],
    "b": ["Badger", "Beaver", "Bison", "Bumblebee", "Barracuda", "Bat"],
    "c": ["Capybara", "Cheetah", "Chinchilla", "Cobra", "Crab", "Chameleon", "Coyote"],
    "d": ["Dolphin", "Dingo", "Dodo", "Duck", "Dragonfly", "Donkey"],
    "e": ["Eagle", "Eel", "Elephant", "Emu", "Ermine"],
    "f": ["Ferret", "Flamingo", "Fox", "Frog", "Falcon", "Firefly"],
    "g": ["Gazelle", "Gecko", "Giraffe", "Gorilla", "Goose", "Gopher"],
    "h": ["Hamster", "Hedgehog", "Hippo", "Hummingbird", "Hyena", "Heron"],
    "i": ["Ibex", "Iguana", "Impala", "Ibis"],
    "j": ["Jackal", "Jaguar", "Jellyfish", "Jay"],
    "k": ["Kangaroo", "Koala", "Kingfisher", "Kiwi", "Kitten", "Krill"],
    "l": ["Lizard", "Llama", "Lemur", "Lobster", "Leopard", "Lynx"],
    "m": ["Meerkat", "Mongoose", "Moose", "Marmot", "Manatee", "Macaw"],
    "n": ["Narwhal", "Newt", "Nightingale", "Numbat"],
    "o": ["Octopus", "Otter", "Owl", "Ostrich", "Orca", "Ocelot"],
    "p": ["Panda", "Penguin", "Platypus", "Puffin", "Porcupine", "Pelican"],
    "q": ["Quail", "Quokka", "Quetzal"],
    "r": ["Raccoon", "Reindeer", "Rhino", "Robin", "Raven", "Rabbit"],
    "s": ["Sloth", "Squirrel", "Seal", "Salamander", "Seahorse", "Stork"],
    "t": ["Tapir", "Tiger", "Toucan", "Turtle", "Tortoise", "Tarsier"],
    "u": ["Unicorn", "Urchin", "Uakari"],
    "v": ["Vulture", "Vole", "Viper", "Vicuna"],
    "w": ["Walrus", "Wombat", "Wolf", "Weasel", "Wallaby", "Woodpecker"],
    "y": ["Yak", "Yeti", "Yellowjacket"],
    "z": ["Zebra", "Zebu", "Zorilla"],
}


def main() -> None:
    names = sorted({f"{a} {n}" for letter, adjs in ADJECTIVES.items()
                    for a in adjs for n in ANIMALS.get(letter, [])})
    out = Path(__file__).resolve().parent.parent / "backend/src/padlet/data/names.txt"
    out.write_text("\n".join(names) + "\n", encoding="utf-8")
    print(f"wrote {len(names)} names to {out}")


if __name__ == "__main__":
    main()
