// Demo board (board.html?b=demo): a pretend lecture with made-up slides, comments and likes.
// Everything happens in this browser tab only: nothing is sent to the server or saved, so it can't be spammed.
// board.js swaps its API calls for demoCall() when the board id is "demo".
import { ApiError } from "./api.js";

export const DEMO_ID = "demo";

const NAMES = ["Peppy Pelican", "Mellow Moose", "Quirky Quokka", "Jolly Jellyfish", "Zesty Zebra", "Nifty Newt", "Plucky Puffin", "Sassy Salamander"];
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();

let nextId = 1;
const staffReply = (text, min) => ({ comment_id: `dc${nextId++}`, text, author_name: "Prof. Alice", is_staff: true, created_at: ago(min), likes: 0, liked: false, mine: false, answered: false, pinned: false, hidden: false });
const comment = (slide_id, author, text, likes, min, extra = {}, replies = []) => ({
  comment_id: `dc${nextId++}`, slide_id, text, author_name: author, is_staff: false, created_at: ago(min),
  likes, liked: false, mine: false, answered: false, pinned: false, hidden: false, replies, ...extra });
const reply = (author, text, likes, min) => ({ ...staffReply(text, min), author_name: author, is_staff: false, likes });

const slide = (n, likes, comments) => ({
  slide_id: `ds${n}`, position: n - 1, image_url: `img/demo/slide-${n}.svg`, width: 1600, height: 900, source: "demo",
  likes, liked: false, comments: comments.map((c) => ({ ...c, slide_id: `ds${n}` })) });

const store = {
  board: { board_id: DEMO_ID, space_id: "demo", name: "Statistics I · Lecture 4 (demo)", locked: false, background: "linen",
    created_at: ago(60 * 30), updated_at: ago(60 * 3), url: "", last_activity_at: ago(8), last_activity_by: null, timezone: "Europe/Berlin", next_switch_at: null },
  slides: [
    slide(1, 5, [
      comment("", "Lazy Lizard", "Will the slides be online before the exercise session?", 3, 60 * 26, { answered: true }, [
        staffReply("Yes, they're on this board already. After class I'll also put the PDF with all your questions on OLAT.", 60 * 25)]),
    ]),
    slide(2, 12, [
      comment("", "Curious Capybara", "Why n − 1 and not n? I've seen it in the book but never understood it.", 11, 60 * 22, { pinned: true, answered: true }, [
        staffReply("Great question, pinned! Short version: x̄ is always closer to your data than the true mean μ, so dividing by n would underestimate the variance. We'll go through it on the board on Tuesday.", 60 * 20),
        reply("Mellow Moose", "That finally makes sense, thanks!", 4, 60 * 19)]),
      comment("", "Dizzy Dolphin", "Why do we square the deviations and not just use absolute values?", 6, 60 * 15, {}, [
        reply("Grumpy Giraffe", "Squares are easier to work with mathematically and punish big deviations more.", 2, 60 * 14)]),
      comment("", "Sneaky Squirrel", "Could we get a small numeric example for s²?", 2, 60 * 9),
    ]),
    slide(3, 8, [
      comment("", "Wobbly Walrus", "How large is “large n” for the CLT? Is it always 30?", 9, 60 * 12),
      comment("", "Bashful Badger", "Is the standard error the same as the standard deviation?", 4, 60 * 10, { answered: true }, [
        staffReply("Not quite: s describes the spread of the data, the standard error the spread of the sample mean (s / √n).", 60 * 8)]),
    ]),
    slide(4, 10, [
      comment("", "Cheeky Chameleon", "So it's wrong to say “there's a 95 % chance that μ is in the interval”? My tutor said the opposite.", 14, 60 * 7, {}, [
        reply("Fuzzy Flamingo", "I'm confused by this too 🙋", 3, 60 * 6)]),
      comment("", "Fuzzy Flamingo", "Why are two of the intervals red? Did we do something wrong in those samples?", 5, 60 * 5),
    ]),
    slide(5, 4, [
      comment("", "Grumpy Giraffe", "When do I use t and when z in the exam?", 7, 60 * 4, { answered: true }, [
        staffReply("Whenever σ is unknown, which is almost always, use t. We'll practise this in the exercise session.", 60 * 3)]),
    ]),
    slide(6, 6, [
      comment("", "Sleepy Sloth", "Where does 2.064 come from? A table?", 2, 40),
      comment("", "Jumpy Jaguar", "Could you show the calculation in R next time?", 1, 25),
    ]),
  ],
};

export function demoIdentity() {
  const name = NAMES[Math.floor(Math.random() * NAMES.length)];
  return { anon_id: "demo-viewer", name, token: "demo", expires_at: Math.floor(Date.now() / 1000) + 7 * 86400 };
}

const find = (id) => {
  for (const s of store.slides) {
    if (s.slide_id === id) return { item: s };
    for (const c of s.comments) {
      if (c.comment_id === id) return { item: c, list: s.comments };
      const r = c.replies.find((x) => x.comment_id === id);
      if (r) return { item: r, list: c.replies };
    }
  }
  return {};
};

// Same shapes as the real API (see boards.view / add_comment / toggle_like).
export async function demoCall(method, path, body, viewerName) {
  if (method === "GET" && path === `/api/boards/${DEMO_ID}`) {
    return structuredClone({ board: store.board, slides: store.slides, my_role: null, upcoming: null, viewer: { name: viewerName, is_user: false } });
  }
  if (method === "POST" && path.endsWith("/comments")) {
    const text = String(body?.text || "").trim();
    if (!text) throw new ApiError(400, "invalid_input", "Please write something");
    const c = { comment_id: `dc${nextId++}`, slide_id: body.slide_id, text: text.slice(0, 2000), author_name: viewerName, is_staff: false,
      created_at: new Date().toISOString(), likes: 0, liked: false, mine: true, answered: false, pinned: false, hidden: false, replies: [] };
    if (body.parent_id) {
      const { item: parent } = find(body.parent_id);
      if (!parent) throw new ApiError(404, "not_found", "Comment not found");
      c.parent_id = parent.comment_id; c.slide_id = parent.slide_id; delete c.replies;
      parent.replies.push(c);
    } else {
      find(body.slide_id).item?.comments.push(c);
    }
    return { comment: structuredClone(c) };
  }
  if (method === "POST" && path.endsWith("/likes")) {
    const { item } = find(body?.slide_id || body?.comment_id);
    if (!item) throw new ApiError(404, "not_found", "Not found");
    item.liked = !item.liked; item.likes += item.liked ? 1 : -1;
    return { liked: item.liked, likes: item.likes };
  }
  const del = path.match(/\/comments\/([^/]+)$/);
  if (method === "DELETE" && del) {
    const { item, list } = find(del[1]);
    if (!item?.mine) throw new ApiError(403, "forbidden", "You can only delete your own comments in the demo");
    list.splice(list.indexOf(item), 1);
    return null;
  }
  throw new ApiError(403, "demo", "Not available in the demo board");
}
