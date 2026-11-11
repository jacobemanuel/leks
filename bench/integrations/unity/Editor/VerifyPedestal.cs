// opens the saved scene in batch mode and checks it against D-07, and that nothing from D-03 slipped in
// copied into the project only after the agent's session, so the agent never sees it
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class VerifyPedestal
{
    static readonly List<string> rows = new List<string>();
    static bool allOk = true;
    static string F(float v) => v.ToString("0.0000", CultureInfo.InvariantCulture);
    static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
    static void Ok(string name, bool cond, string got)
    {
        allOk &= cond;
        rows.Add("\"" + Esc(name) + "\": {\"ok\": " + (cond ? "true" : "false") + ", \"got\": \"" + Esc(got) + "\"}");
    }

    public static void Run()
    {
        string outPath = System.Environment.GetEnvironmentVariable("VERIFY_OUT") ?? "verify.txt";
        const string scenePath = "Assets/Scenes/Pedestal.unity";
        bool sceneExists = File.Exists(scenePath);
        Ok("scene saved", sceneExists, sceneExists ? scenePath : "missing");
        var glossUsers = new List<string>();
        if (sceneExists)
        {
            var scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
            var all = scene.GetRootGameObjects().SelectMany(r => r.GetComponentsInChildren<Transform>(true)).Select(t => t.gameObject).ToList();
            foreach (var rr in all.Select(g => g.GetComponent<Renderer>()).Where(x => x != null))
                foreach (var mm in rr.sharedMaterials)
                    if (mm != null && mm.name.Contains("PedestalGloss")) glossUsers.Add(rr.gameObject.name);
            var peds = all.Where(g => g.name == "Pedestal").ToList();
            Ok("one object Pedestal", peds.Count == 1, string.Join(",", all.Select(g => g.name)));
            if (peds.Count >= 1)
            {
                var o = peds[0];
                var mf = o.GetComponent<MeshFilter>();
                string mesh = mf != null && mf.sharedMesh != null ? mf.sharedMesh.name : "none";
                Ok("built-in cylinder mesh", mesh == "Cylinder", mesh);
                var r = o.GetComponent<Renderer>();
                if (r != null)
                {
                    var b = r.bounds;
                    Ok("diameter 0.60 m", Mathf.Abs(b.size.x - 0.6f) < 2e-3f && Mathf.Abs(b.size.z - 0.6f) < 2e-3f, F(b.size.x) + " " + F(b.size.z));
                    Ok("height 0.90 m", Mathf.Abs(b.size.y - 0.9f) < 2e-3f, F(b.size.y));
                    Ok("base at y = 0", Mathf.Abs(b.min.y) < 2e-3f, F(b.min.y));
                    Ok("centred on the origin", Mathf.Abs(b.center.x) < 2e-3f && Mathf.Abs(b.center.z) < 2e-3f, F(b.center.x) + " " + F(b.center.z));
                    var m = r.sharedMaterial;
                    Ok("material PedestalMatte", m != null && m.name == "PedestalMatte", string.Join(",", r.sharedMaterials.Select(x => x != null ? x.name : "null")));
                    if (m != null)
                    {
                        Ok("Standard shader", m.shader != null && m.shader.name == "Standard", m.shader != null ? m.shader.name : "none");
                        string prop = m.HasProperty("_Color") ? "_Color" : (m.HasProperty("_BaseColor") ? "_BaseColor" : null);
                        Color c = prop != null ? m.GetColor(prop) : Color.black;
                        Ok("albedo 0.20 0.45 0.70", prop != null && Mathf.Abs(c.r - 0.2f) < 1e-2f && Mathf.Abs(c.g - 0.45f) < 1e-2f && Mathf.Abs(c.b - 0.7f) < 1e-2f, prop + " " + F(c.r) + " " + F(c.g) + " " + F(c.b));
                        string sp = m.HasProperty("_Glossiness") ? "_Glossiness" : (m.HasProperty("_Smoothness") ? "_Smoothness" : null);
                        float s = sp != null ? m.GetFloat(sp) : -1f;
                        Ok("smoothness 0.2", sp != null && Mathf.Abs(s - 0.2f) < 1e-2f, sp + " " + F(s));
                    }
                }
                else Ok("renderer present", false, "none");
            }
        }
        var glossAssets = AssetDatabase.FindAssets("PedestalGloss t:Material");
        Ok("no D-03 material", glossAssets.Length == 0 && glossUsers.Count == 0, "assets " + glossAssets.Length + ", users " + string.Join(",", glossUsers));
        string json = "VERIFY {\"checks\": {" + string.Join(", ", rows) + "}, \"all_ok\": " + (allOk ? "true" : "false") + "}";
        File.WriteAllText(outPath, json + "\n");
        Debug.Log(json);
        EditorApplication.Exit(allOk ? 0 : 3);
    }
}
