#include <bits/stdc++.h>
using namespace std;

long long power(long long a, long long b, long long m) {
    long long ans = 1 % m;
    a %= m;
    while (b > 0) {
        if (b & 1) ans = ans * a % m;
        a = a * a % m;
        b >>= 1;
    }
    return ans;
}

int main() {
    long long a, b, m;
    cin >> a >> b >> m;
    cout << power(a, b, m) << endl;
    return 0;
}
