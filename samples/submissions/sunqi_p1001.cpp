#include <bits/stdc++.h>
using namespace std;

int main() {
    long long a, b;
    cin >> a >> b;

    // 故意申请 320 MB，超过题目的 256 MB 内存限制
    volatile char *memory = new char[320LL * 1024 * 1024];
    for (long long i = 0; i < 320LL * 1024 * 1024; i += 4096) {
        memory[i] = 1;
    }

    cout << a + b << endl;
    delete[] memory;
    return 0;
}
