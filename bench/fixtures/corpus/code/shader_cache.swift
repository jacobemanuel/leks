import Foundation

// persistent shader cache, toy example code

struct ShaderKey: Hashable {
    let sourceHash: String
    let flags: UInt32
}

func cacheDirectory() -> URL {
    // compiled shaders live in the user cache folder
    let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
    return base.appendingPathComponent("shaders", isDirectory: true)
}

final class ShaderCache {
    var entries: [ShaderKey: Data] = [:]
    let maxBytes = 200 * 1024 * 1024

    func lookup(_ key: ShaderKey) -> Data? {
        return entries[key]
    }

    func store(_ key: ShaderKey, binary: Data) {
        entries[key] = binary
    }

    func evictStale(olderThan days: Int) -> Int {
        // stub: a real version walks the folder and checks dates
        return 0
    }
}
