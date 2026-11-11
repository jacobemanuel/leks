// sets the mcp for unity prefs the batch test needs: stdio transport, no telemetry, no client auto-register
using UnityEditor;
public static class LeksSetup
{
    public static void Run()
    {
        EditorPrefs.SetBool("MCPForUnity.UseHttpTransport", false);
        EditorPrefs.SetBool("MCPForUnity.TelemetryDisabled", true);
        EditorPrefs.SetBool("MCPForUnity.AutoRegisterEnabled", false);
        UnityEngine.Debug.Log("LEKSSETUP http=" + EditorPrefs.GetBool("MCPForUnity.UseHttpTransport", true) + " telemetry_off=" + EditorPrefs.GetBool("MCPForUnity.TelemetryDisabled", false) + " autoreg=" + EditorPrefs.GetBool("MCPForUnity.AutoRegisterEnabled", true));
        EditorApplication.Exit(0);
    }
    // undo after the test so nothing of it stays in the editor prefs
    public static void Clear()
    {
        foreach (var k in new[] { "MCPForUnity.UseHttpTransport", "MCPForUnity.TelemetryDisabled", "MCPForUnity.AutoRegisterEnabled" }) EditorPrefs.DeleteKey(k);
        UnityEngine.Debug.Log("LEKSSETUP cleared");
        EditorApplication.Exit(0);
    }
}
