using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Santo Patent OCR")]
[assembly: AssemblyProduct("Santo Patent OCR")]
[assembly: AssemblyCompany("Santo")]
[assembly: AssemblyDescription("Offline CPU launcher for Santo Patent OCR")]
[assembly: AssemblyVersion("1.5.1.0")]
[assembly: AssemblyFileVersion("1.5.1.0")]

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string runtime = Path.Combine(root, "runtime");
        string app = Path.Combine(root, "app");
        string main = Path.Combine(app, "main.py");
        bool selfTest = Array.IndexOf(args, "--offline-self-test") >= 0;
        string python = Path.Combine(runtime, selfTest ? "python.exe" : "pythonw.exe");

        if (!File.Exists(python) || !File.Exists(main))
        {
            MessageBox.Show(
                "離線執行環境不完整。\n\n請確認 SantoPatentOCR.exe、runtime 和 app 都放在同一資料夾內，不能只複製 EXE。",
                "Santo Patent OCR",
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

            string oldPath = startInfo.EnvironmentVariables["PATH"] ?? "";
            startInfo.EnvironmentVariables["PATH"] = String.Join(";", new[]
            {
                runtime,
                Path.Combine(runtime, "Library", "bin"),
                Path.Combine(runtime, "Scripts"),
                oldPath
            });
            startInfo.EnvironmentVariables["CONDA_PREFIX"] = runtime;
            startInfo.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            startInfo.EnvironmentVariables["PYTHONUTF8"] = "1";
            startInfo.EnvironmentVariables["KMP_DUPLICATE_LIB_OK"] = "TRUE";
            startInfo.EnvironmentVariables["OMP_NUM_THREADS"] = "1";
            startInfo.EnvironmentVariables["MKL_NUM_THREADS"] = "1";
            startInfo.EnvironmentVariables["YOLO_CONFIG_DIR"] = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "SantoPatentOCR");

            using (Process child = Process.Start(startInfo))
            {
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(
                "程式無法啟動：\n\n" + error.Message,
                "Santo Patent OCR",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 3;
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
