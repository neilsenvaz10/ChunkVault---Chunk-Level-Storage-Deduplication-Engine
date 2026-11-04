// Pure comparison of two ordered chunk manifests ([{chunk_index, chunk_hash, chunk_size}]).
// Everything is derived from the manifests passed in; nothing is estimated.

export function compareManifests(a, b) {
  const sizeOf = new Map()
  const posA = new Map()
  const posB = new Map()
  const add = (map, list) =>
    list.forEach((c) => {
      sizeOf.set(c.chunk_hash, c.chunk_size)
      if (!map.has(c.chunk_hash)) map.set(c.chunk_hash, [])
      map.get(c.chunk_hash).push(c.chunk_index)
    })
  add(posA, a)
  add(posB, b)

  const common = [...posA.keys()].filter((h) => posB.has(h))
  const commonSet = new Set(common)
  const sum = (list) => list.reduce((n, c) => n + c.chunk_size, 0)
  const totalA = sum(a)
  const totalB = sum(b)
  const reusedBytesA = sum(a.filter((c) => commonSet.has(c.chunk_hash)))
  const reusedBytesB = sum(b.filter((c) => commonSet.has(c.chunk_hash)))

  const commonChunks = common
    .map((h) => ({ hash: h, size: sizeOf.get(h), indexesA: posA.get(h), indexesB: posB.get(h) }))
    .sort((x, y) => x.indexesA[0] - y.indexesA[0])

  // Position shift of each shared chunk (first occurrence): equal shifts mean the shared
  // chunks kept their relative order, which is what content-defined boundaries give after an insertion.
  const shifts = [...new Set(commonChunks.map((c) => c.indexesB[0] - c.indexesA[0]))].sort((x, y) => x - y)
  const inOrder = commonChunks.every((c, i) => i === 0 || c.indexesB[0] >= commonChunks[i - 1].indexesB[0])

  return {
    commonDistinct: common.length,
    onlyADistinct: posA.size - common.length,
    onlyBDistinct: posB.size - common.length,
    sharedPayloadBytes: commonChunks.reduce((n, c) => n + c.size, 0),
    totalA,
    totalB,
    reusedBytesA,
    reusedBytesB,
    reusePctA: totalA ? (100 * reusedBytesA) / totalA : null,
    reusePctB: totalB ? (100 * reusedBytesB) / totalB : null,
    commonChunks,
    shifts,
    sharedChunksKeepRelativeOrder: inOrder,
    isCommon: (hash) => commonSet.has(hash),
  }
}
