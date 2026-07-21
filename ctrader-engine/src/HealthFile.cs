namespace ApexVoid.CTraderFeed;

public sealed class HealthFile(string path)
{
  public void Touch()
  {
    var dir = Path.GetDirectoryName(path);
    if (!string.IsNullOrWhiteSpace(dir))
    {
      Directory.CreateDirectory(dir);
    }
    File.WriteAllText(path, DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString());
  }

  public static int Check(string path, TimeSpan maxAge)
  {
    if (!File.Exists(path))
    {
      return 1;
    }
    var age = DateTimeOffset.UtcNow - File.GetLastWriteTimeUtc(path);
    return age <= maxAge ? 0 : 1;
  }
}
