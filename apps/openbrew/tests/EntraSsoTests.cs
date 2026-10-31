using System;
using System.Text;

public static class EntraSsoTests
{
    public static int Main()
    {
        var credential = "Basic " + Convert.ToBase64String(Encoding.UTF8.GetBytes("sam:proxy-test-secret"));
        Check(EntraSso.IsAllowed(credential, "sam@cooked.beer", "proxy-test-secret", "sam@cooked.beer"), "assigned user");
        Check(!EntraSso.IsAllowed(null, "sam@cooked.beer", "proxy-test-secret", "sam@cooked.beer"), "spoofed email without credential");
        Check(!EntraSso.IsAllowed(credential, "someone@cooked.beer", "proxy-test-secret", "sam@cooked.beer"), "unassigned user");
        Check(!EntraSso.IsAllowed(credential, "sam@cooked.beer", "wrong-secret", "sam@cooked.beer"), "wrong credential");
        Check(!EntraSso.IsAllowed("Basic invalid", "sam@cooked.beer", "proxy-test-secret", "sam@cooked.beer"), "malformed credential");
        Check(!EntraSso.IsAllowed(credential, "sam@cooked.beer", "", "sam@cooked.beer"), "missing configuration");
        Console.WriteLine("All six authentication boundary checks passed.");
        return 0;
    }

    static void Check(bool result, string description)
    {
        if (!result) throw new Exception("Failed: " + description);
    }
}
