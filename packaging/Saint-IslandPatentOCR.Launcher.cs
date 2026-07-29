using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Saint-Island_Patent_MDS")]
[assembly: AssemblyProduct("Saint-Island_Patent_MDS")]
[assembly: AssemblyCompany("Saint-Island")]
[assembly: AssemblyDescription("Offline CPU launcher for Saint-Island Patent OCR")]
[assembly: AssemblyVersion("1.9.0.0")]
[assembly: AssemblyFileVersion("1.9.0.0")]

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        string diagnosticLog = Path.Combine(
            Path.GetTempPath(), "Saint-Island_Patent_MDS_launcher.log");
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string runtime = Path.Combine(root, "runtime");
        string app = Path.Combine(root, "app");
        string main = Path.Combine(app, "main.py");
        bool selfTest = Array.IndexOf(args, "--offline-self-test") >= 0;
        bool guiSmokeTest = Array.IndexOf(args, "--gui-smoke-test") >= 0;
        bool automatedTest = selfTest || guiSmokeTest;
        string python = Path.Combine(runtime, selfTest ? "python.exe" : "pythonw.exe");

        WriteDiagnostic(diagnosticLog, "Launcher started. Root=" + root);
        WriteDiagnostic(diagnosticLog, "Python=" + python + " Main=" + main);

        if (!File.Exists(python) || !File.Exists(main))
        {
            string message = "離線執行環境不完整。\n\n請確認 Saint-Island_Patent_MDS.exe、runtime 和 app 都放在同一資料夾內，不能只複製 EXE。";
            WriteDiagnostic(diagnosticLog, message);
            if (!automatedTest)
                MessageBox.Show(
                    message,
                    "Saint-Island_Patent_MDS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            return 2;
        }

        try
        {
            var forwarded = new List<string> { main };
            forwarded.AddRange(args);

            var startInfo = new ProcessStartInfo
            {
                FileName = python,
                Arguments = JoinArguments(forwarded),
                WorkingDirectory = app,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };

            // Do not touch ProcessStartInfo.EnvironmentVariables here. Some
            // managed company environments expose both "Path" and "PATH";
            // .NET Framework then throws while constructing that dictionary.
            // Updating this launcher's own environment is inherited by the
            // child and remains robust in that duplicate-key situation.
            string oldPath = Environment.GetEnvironmentVariable("PATH") ?? "";
            Environment.SetEnvironmentVariable("PATH", String.Join(";", new[]
            {
                runtime,
                Path.Combine(runtime, "Library", "bin"),
                Path.Combine(runtime, "Scripts"),
                oldPath
            }));
            Environment.SetEnvironmentVariable("CONDA_PREFIX", runtime);
            Environment.SetEnvironmentVariable("PYTHONNOUSERSITE", "1");
            Environment.SetEnvironmentVariable("PYTHONUTF8", "1");
            Environment.SetEnvironmentVariable("KMP_DUPLICATE_LIB_OK", "TRUE");
            Environment.SetEnvironmentVariable("OMP_NUM_THREADS", "1");
            Environment.SetEnvironmentVariable("MKL_NUM_THREADS", "1");
            Environment.SetEnvironmentVariable("YOLO_CONFIG_DIR", Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Saint-IslandPatentOCR"));

            using (Process child = Process.Start(startInfo))
            {
                WriteDiagnostic(diagnosticLog, "Child started. PID=" + child.Id);
                child.WaitForExit();
                WriteDiagnostic(diagnosticLog, "Child exit=" + child.ExitCode);
                return child.ExitCode;
            }
        }
        catch (Exception error)
        {
            WriteDiagnostic(diagnosticLog, error.ToString());
            if (!automatedTest)
                MessageBox.Show(
                    "程式無法啟動：\n\n" + error.Message,
                    "Saint-Island_Patent_MDS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            return 3;
        }
    }

    private static void WriteDiagnostic(string path, string message)
    {
        try
        {
            File.AppendAllText(
                path,
                DateTime.Now.ToString("s") + " " + message + Environment.NewLine,
                Encoding.UTF8);
        }
        catch
        {
            // Diagnostics must never prevent the application from starting.
        }
    }

    private static string JoinArguments(IEnumerable<string> arguments)
    {
        var result = new StringBuilder();
        foreach (string argument in arguments)
        {
            if (result.Length > 0)
                result.Append(' ');
            result.Append(QuoteArgument(argument));
        }
        return result.ToString();
    }

    private static string QuoteArgument(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0)
            return value;

        var result = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char character in value)
        {
            if (character == '\\')
            {
                backslashes++;
                continue;
            }

            if (character == '"')
            {
                result.Append('\\', backslashes * 2 + 1);
                result.Append('"');
                backslashes = 0;
                continue;
            }

            result.Append('\\', backslashes);
            backslashes = 0;
            result.Append(character);
        }
        result.Append('\\', backslashes * 2);
        result.Append('"');
        return result.ToString();
    }
}
