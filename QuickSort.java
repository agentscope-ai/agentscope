
public class QuickSort {

    /**
     * 快速排序入口
     */
    public static void quickSort(int[] arr) {
        if (arr == null || arr.length <= 1) {
            return;
        }
        quickSort(arr, 0, arr.length - 1);
    }

    /**
     * 递归快排 [left, right]
     */
    private static void quickSort(int[] arr, int left, int right) {
        if (left >= right) {
            return;
        }
        int pivotIndex = partition(arr, left, right);
        quickSort(arr, left, pivotIndex - 1);
        quickSort(arr, pivotIndex + 1, right);
    }

    /**
     * 分区：以 arr[right] 为 pivot，小于 pivot 的放左边，大于的放右边
     * @return pivot 最终位置
     */
    private static int partition(int[] arr, int left, int right) {
        int pivot = arr[right];   // 选最右元素为基准
        int i = left - 1;         // i 指向"小于 pivot 的区域"的最后一个元素

        for (int j = left; j < right; j++) {
            if (arr[j] < pivot) {
                i++;
                swap(arr, i, j);
            }
        }
        // 将 pivot 放到正确位置
        swap(arr, i + 1, right);
        return i + 1;
    }

    private static void swap(int[] arr, int i, int j) {
        int tmp = arr[i];
        arr[i] = arr[j];
        arr[j] = tmp;
    }

    // ==================== 测试 ====================
    public static void main(String[] args) {
        int[] arr1 = {5, 2, 8, 1, 9, 3, 7, 4, 6};
        int[] arr2 = {3, 3, 3, 3};
        int[] arr3 = {1};
        int[] arr4 = {};
        int[] arr5 = {9, 8, 7, 6, 5, 4, 3, 2, 1};

        System.out.println("排序前: " + arrayToString(arr1));
        quickSort(arr1);
        System.out.println("排序后: " + arrayToString(arr1));

        quickSort(arr2);
        System.out.println("全相等: " + arrayToString(arr2));

        quickSort(arr3);
        System.out.println("单元素: " + arrayToString(arr3));

        quickSort(arr4);
        System.out.println("空数组: " + arrayToString(arr4));

        quickSort(arr5);
        System.out.println("逆序:   " + arrayToString(arr5));
    }

    private static String arrayToString(int[] arr) {
        if (arr == null) return "null";
        if (arr.length == 0) return "[]";
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            sb.append(arr[i]);
            if (i < arr.length - 1) sb.append(", ");
        }
        return sb.append("]").toString();
    }
}
